# !/usr/bin/env python
# encoding: utf-8
"""
Implementation of the GLEaM model. See #reference article:

Balcan, Duygu, Bruno Gonçalves, Hao Hu, José J. Ramasco, Vittoria Colizza,
and Alessandro Vespignani. 2010.
“Modeling the Spatial Spread of Infectious Diseases: The GLobal Epidemic and
Mobility Computational Model.”
Journal of Computational Science 1 (3): 132–45.
"""

from collections import Counter
from copy import deepcopy
import math
import numpy as np
import networkx as nx
from datetime import date, datetime, timedelta
import json
from time import strftime
import cProfile
import pickle
from pprint import pprint


class Model(nx.DiGraph):
    """
    Uses nx.DiGraph to implement the network structure, and extends it with methods
    to run the simulations.

    Each node is a dict with values for each subpopulation compartment, ie
    susceptible, latent, infectious asymptomatic, infectious allowed to travel,
    infectious travel restricted, and recovered.

    Each edge is a commuting route between two nodes. Commuting rates between two
    subpopulations are encoded with the commuting_rate attribute for each edge.

    Attributes:
        commuting_return_rate (float): tau parameter - 1 / days spent out
        p_asymptomatic (float): probability of asymptomatic infection
        p_exit_latent (float): probability of exiting latent compartment
        p_recovery (float): probability of exiting infectious compartments
        p_travel_allowed (float): probability of being allowed to travel while infectious
    """

    def __init__(self, subpop_network, params):
        """
        Args:
            subpop_network (nx.DiGraph): graph representation of the subpopulations
                                         and their commuting relationships
            params (dict): model parameters

        Raises:
            ValueError: Description
            ValueError
            param dict does not contain all required values
        """
        for param in ['p_exit_latent', 'p_recovery', 'commuting_return_rate',
                      'starting_date', 'r0']:
            if param not in params.keys():
                raise ValueError("Missing {} parameter".format(param))

        super(Model, self).__init__(subpop_network)

        self.p_exit_latent = 1 / params['latent_period']
        self.p_exit_inf = params['infectous_period']
        self.tau = params['tau']
        self.starting_date = params['starting_date']
        self.r0 = params['r0']
        self.beta = params['r0'] / params['infectious_period']

        # Round up population values
        for i in self.nodes_iter():
            self.node[i]['pop'] = math.ceil(self.node[i]['pop'])

        # Precompute sigma_by_tau and sigma_ij_by_tau values
        # sigma for a given node stands for total outgoing commuting rate
        # sigma_ij stands for commuting rate of node i towards node j
        for i in self.nodes_iter():
            sigma_by_tau = sum([self.edge[i][j]['cr'] 
                                for j in self.successors(source)]) / self.tau
            self.node[i]['sigma_by_tau'] = sigma_by_tau
            for j in self.successors(source):
                self.edge[i][j]['sigma_ij_by_tau'] = self.edge[i][j]['commuting_rate'] / self.tau

        # Precompute effective population for every node
        for j in self.nodes_iter():
            local_pop = self.node[j]['pop'] / (1 + self.node[j]['sigma_by_tau'])
            other_pop = sum([self.node[i]['pop'] * (self.edge[i][j]['sigma_ij_by_tau'] /
                                                    (1 + self.node[i]['sigma_by_tau'])
                                                   )
                             for i in self.predecessors(j)])
            self.node[j]['effective_population'] = local_pop + other_pop

        # Precompute the beta coefficient divided by the effective population
        # for every node
        for i in self.nodes_iter():
            self.node[i]['beta_by_n*'] = self.beta / self.node[i]['effective_population']

        # Initialize compartments
        for j in self.nodes_iter():
            self.node[j]['compartments'] = Counter(
                                           {'S': self.node[j]['pop'],
                                            'E': 0,
                                            'I': 0,
                                            'Ŗ': 0
                                            })
            # Store state at beginning.
            self.node[j]['history'] = [deepcopy(self.node[j]['compartments'])]
            self.node[j]['exit_rate'] = self.get_exit_rate(j)

    def compute_long_distance_travels(self):
        """
        Redistributes travelers among neighboring nodes, according to
        long-distance traffic information.
        """
        # TODO: implement this
        pass

    def draw_new_infectious_counts(self, node_id):
        """
        First extract probabilities for asymptomatic, travelling and non travelling
        infectious among those that leave latent state, then scale by probability
        to exit latent state.

        Args:
            node_id (int): id of the nx.DiGraph node

        Returns:
            list of floats: Transition probabilies from latent to each infectious
                            compartments.
        """
        if self.node[node_id]['compartments']['latent'] < 1:
            return 0
        return np.random.binomial(self.node[node_id]['compartments']['latent'],
                                  self.p_exit_latent)
    
    def draw_new_latent_count(self, node_id):
        """
        For a given node, draws a random value from a binomial distribution
        defined by susceptible population and chance of contagion.

        Args:
            node_id (int): id of the nx.DiGraph node

        Returns:
            float: Probability extracted from the binomial distribution
        """
        if self.node[node_id]['compartments']['susceptible'] < 1:
            return 0
        return np.random.binomial(self.node[node_id]['compartments']['susceptible'],
                                  self.effective_force_of_infection(node_id))

    def draw_new_recovered_counts(self, node_id):
        """
        For a given node, draw from a binomial distribution the number of infectious
        people that will recover in next time step.
        """
        if self.node[node_id]['compartments']['infectious'] < 1:
            return 0
        return np.random.binomial(self.node['node_id']['compartments']['infectious'],
                                  self.p_recovery)

    def effective_force_of_infection(self, node_id):
        """For given subpopulation node, computes effective for of infection,
        taking into account two terms:
            1)  the local force of infection;
            2)  the forces of infection in neighboring nodes, scaled respectively
                by the amount of people from local node that commute to these neighbors.
        Args:
            node_id (int): id of the nx.DiGraph node
        Returns:
            float: Effective force of infection at given node.
        """
        local_exit_rate = self.node[node_id]['exit_rate']
        local_foi = self.node[node_id]['foi'] / (1 + local_exit_rate / self.return_rate)

        nbs_foi = 0
        # Only consider neighbors that can be attained from local node, ie successors
        for nb_id in self.successors(node_id):
            effective_commuting_rate = self.edge[node_id][nb_id]['commuting_rate'] / self.return_rate
            nbs_foi += (self.node[nb_id]['foi'] * effective_commuting_rate /
                        (1 + local_exit_rate / self.return_rate))

        return local_foi + nbs_foi

    def effective_population(self, node_id):
        """
        For given subpopulation node, compute effective total population,
        taking into account the commuting rates between neighboring subpopulations.

        Args:
            node_id (int): id of the nx.DiGraph node

        Returns:
            int: Effective population of given node.
        """
        node_pop = self.node[node_id]['compartments']
        local_exit_rate = self.node[node_id]['exit_rate']
        local_pop = (node_pop['infectious_nt'] +
                     (sum(node_pop.values()) - node_pop['infectious_nt']) /
                     (1 + local_exit_rate / self.return_rate))

        other_pop = 0
        # Only consider neighbors that have an edge inbound to local node, ie predecessors
        for nb_id in self.predecessors(node_id):
            nb_pop = self.node[nb_id]['compartments']
            nb_exit_rate = self.node[nb_id]['exit_rate']
            other_pop += (((sum(nb_pop.values()) - node_pop['infectious_nt']) /
                           (1 + nb_exit_rate / self.return_rate)) *
                          self.edge[nb_id][node_id]['commuting_rate'] / self.return_rate)

        return local_pop + other_pop

    def get_exit_rate(self, node_id):
        """
        Return the commuting exit rate for a given node, as a function of
        commuting and return rates.

        Args:
            node_id (int): id of the nx.DiGraph node
        """
        return (sum([e[2]['commuting_rate']
                     for e in self.out_edges(node_id, data=True)]))

    def infect(self):
        """
        Runs one step of the infection process on every node in the network.
        """
        self.update_effective_foi()
        for node_id in self.nodes_iter():
            new_latent = self.draw_new_latent_count(node_id)
            new_infectious = self.draw_new_infectious_counts(node_id)
            new_recovered = self.draw_new_recovered_counts(node_id)
           
            compartments = self.node[node_id]['compartments']

            compartments['S'] -= new_latent
            compartments['E'] += new_latent - new_infectious
            compartments['I'] += new_infectious - new_recovered
            compartments['R'] += new_recovered

            self.node[node_id]['history'].append(Counter(compartments))

    def rate_of_transmission(self, hemisphere=None):
        """
        Computes the scalar factor to apply on force of infection.

        Args:
            hemisphere (str): either 'north', 'south' or None

        Returns:
            float: rate of transmission for given node, taking seasonality into account

        Raises:
            ValueError: hemisphere argument must be in ['north', 'south', None]
        """
        if hemisphere not in ['north', 'south', None]:
            raise ValueError("hemisphere argument must be in ['north', 'south', None]")
        if hemisphere in ['north', 'south']:
            pass  # TODO: implement this
        if hemisphere is None:
            return self.r0

    def seed_infectious(self, node_id, seeds=1):
        """
        Transfers a number of people from susceptible compartment to an infectious
        compartment at given node_id.

        Args:
            node_id (str): Node in which we want to seed infectious people
            seeds (int): Number of people to be seeded. 1 by default.
            inf_type (str): Specifies the type of infectious to be seeded.
        """
        self.node[node_id]['compartments']['S'] -= seeds
        self.node[node_id]['compartments']['I'] += seeds

    def total_pop(self, node_pop):
        """Returns the total population in a given node population.

        Args:
            node_pop (dict): Contains population values for the different compartments

        Returns:
            int: Sum of values in all compartments
        """
        return sum(node_pop.values())

    def update_effective_foi(self):
        """For a given node, computes the force of infection, taking into account two
        terms:
            1)  the number of infectious people in local node;
            2)  the number of infectious people from neighboring node that commute
                to local node.
        Args:
            node_id (int): id of the nx.DiGraph node
        """
        self.update_local_foi()
        for j_idx in self_nodes_iter():
            local_foi = self.node[j_idx]['local_foi']
            nbs_foi = sum([self.node[i_idx]['local_foi'] *
                           self.edge[j_idx][i_idx]['sigma_ij_by_tau']  # this is actually sigma_ji, the attribute name in this case is misleading
                           for nb_id in self.successors(node_id)])
            
            self.node[node_id]['foi'] = ((local_foi + nbs_foi) /
                                         (1 + self.node[node_id]['sigma_by_tau'])
                                        )

    def update_local_foi(self):
        """
        Updates the localFOI attribute for every node of the graph.
        """        
        # local_inf is the number of infectious people from local node at local node
        # nb_inf is the number of infectious people from neighbor nodes at local node
        for j_idx in self.nodes_iter():
            j = self.node[j_idx]
            local_inf = j['compartments']['I'] / (1 + j['sigma_by_tau'])
            
            nb_inf = 0
            for i_idx in self.predecessors(j_idx):
                i = self.node[i_idx]
                s_ij_by_tau = self.edge[i_idx][j_idx]['sigma_ij_by_tau']
                nb_inf += ((i['compartments']['I'] * s_ij_by_tau) /
                           (1 + i['sigma_by_tau'])
                          )
            total_inf_at_node = local_inf + nb_inf
            self.node[j_idx]['local_foi'] = j['beta_by_n*'] * (total_inf_at_node)

    def vaccinate_node(self, node_id, p_vaccination, vaccine_effectiveness):
        """Substract from the node susceptible compartment the effective number
        of people who are immunized by an immunization campaign.

        Args:
            p_vaccination (float): % of population that gets the vaccine
            vaccine_effectiveness (float): probability that the vaccine actually works
        """
        p_effective_immunization = p_vaccination * vaccine_effectiveness
        immunized = self.node[node_id]['compartments']['S'] * p_effective_immunization
        self.node[node_id]['compartments']['S'] -= immunized
        self.node[node_id]['compartments']['R'] += immunized

    def run_n_simulations(self, n, max_timesteps=200):
        """Using the same starting conditons, will run the infection process
        n times, then for each node will place in history the average value over
        all simulations for each time step.

        Args:
            n (int): Number of simulations to run
        """
        def there_is_infected_nodes(model):
            return any(any(model.node[node_id]['compartments'][comp] != 0
                           for comp in ['E', 'I'])
                       for node_id in model.nodes_iter())
        self.fresh_copy = deepcopy(self)
        new_compartment = {'S': 0,
                           'E': 0,
                           'I': 0,
                           'R': 0}

        for node_id in self.nodes_iter():
            self.node[node_id]['history'] = []
            for i in range(max_timesteps):
                self.node[node_id]['history'].append(Counter(new_compartment))

        for i in range(1, n + 1):
            new_model = deepcopy(self.fresh_copy)
            print(strftime('%H:%M:%S') + '  Simulation #{}'.format(i))
            timestep = 0
            longest_simulation = 0
            while there_is_infected_nodes(new_model):
                timestep += 1
                if timestep > max_timesteps:
                    break
                print(strftime('%H:%M:%S') + '  Timestep #{}'.format(timestep))
                new_model.infect()
                pprint(new_model.node['n890']['compartments'])
            if timestep > longest_simulation:
                longest_simulation = timestep
            # Add compartment values for each node, divided by number of iterations,
            # to model node histories
            for node_id in new_model.nodes_iter():
                history = new_model.node[node_id]['history']
                # Pad each history so that it is as long as the longest history
                history += [history[-1]] * (longest_simulation - len(history))
                for j in range(len(history)):
                    weighted_comps = Counter({k: v / n for k, v in history[j].items()})
                    self.node[node_id]['history'][j] += weighted_comps

    def generate_timestamped_geojson_output(self, output_file):
        """Generates the file to be read by the Leaflet.js visualization script.

        Args:
            output_file (str): Path of the output file
        """

        def generate_point_object(node, state, date):
            return {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [
                                node['lon'],
                                node['lat']
                            ]
                        },
                        "properties": {
                            "name": node['name'],
                            "population": node['pop'],
                            "times": [str(date)],
                            "compartments": {
                                "S": round(state['S']),
                                "E": round(state['E']),
                                "I": round(state['I']),
                                "R": round(state['R'])
                            }
                        }
                    }

        output = []
        for n_id in self.nodes_iter():
            node = self.node[n_id]
            history = self.node[n_id]['history']
            date = self.starting_date
            output.append(generate_point_object(node, history[0], date))

            for i in range(1, len(history) - 1):
                date += timedelta(days=1)
                if history[i] == history[i-1]:
                    output[-1]['properties']['times'].append(str(date))
                else:
                    output.append(generate_point_object(node, history[i], date))

        output_str = 'nodes = ' + json.dumps(output)
        with open(output_file, 'w') as f:
            f.write(output_str)

if __name__ == '__main__':

    now_str = datetime.strftime(datetime.now(), '%m-%d-%Y_%H:%M:%S')

    with open('pygleam_params.json') as f:
        params = json.load(f)

    params['starting_date'] = date(2016, 7, 11)

    gleam = Model(nx.read_graphml(params['graph_data_filepath']), params)

    # Kigali is n890
    # Gisenyi is n1239
    # Musanze is n1451
    # Muhanga is n620
    # Huye is n78
    gleam.vaccinate_node(params['vaccinated_node'],
                         p_vaccination=params['p_vaccinated'],
                         vaccine_effectiveness=params['p_vaccine_effectiveness'])
    
    gleam.seed_infectious(params['starting_node'],
                          seeds=params['seeds'])
    
    gleam.run_n_simulations(n=params['nb_simulations'],
                            max_timesteps=params['timesteps_per_simul'])

    output_file = 'output/node-{}_seed-{}_n-{}_{}.jsonp'.format(params['starting_node'],
                                                                  params['seeds'],
                                                                  params['nb_simulations'],
                                                                  now_str)
    gleam.generate_timestamped_geojson_output(output_file)
