from .termination_condition_base import BaseTerminationCondition
from ..core.catalog import Catalog as c
import math
import random


class UnreachHeading(BaseTerminationCondition):
    """
    UnreachHeading [0, 1]
    End up the simulation if the aircraft didn't reach the target heading or attitude in limited time.
    """

    def __init__(self, config):
        super().__init__(config)
        self.steady_time = self.config.init_config[0]['steady_flight']

    def get_termination(self, task, env, agent_id=0, info={}):
        """
        Return whether the episode should terminate.
        End up the simulation if the aircraft didn't reach the target heading or attitude in limited time.

        Args:
            task: task instance
            env: environment instance

        Returns:Q
            (tuple): (done, success, info)
        """
        done = False
        success = False

        if env.sims[agent_id].get_property_value(c.simulation_sim_time_sec) >= env.sims[agent_id].get_property_value(c.steady_flight):
            if math.fabs(env.sims[agent_id].get_property_value(c.delta_heading)) > 10:
                done = True
            # Change heading every steady_time seconds
            angle = int(env.sims[agent_id].get_property_value(c.steady_flight) / self.steady_time) * 10
            sign = random.choice([+1.0, -1.0])
            new_heading = env.sims[agent_id].get_property_value(c.target_heading_deg) + sign * angle
            new_heading = (new_heading + 360) % 360

            print(f'Time to change: {env.sims[agent_id].get_property_value(c.simulation_sim_time_sec)} (Heading: {env.sims[agent_id].get_property_value(c.target_heading_deg)} -> {new_heading})')

            env.sims[agent_id].set_property_value(c.target_heading_deg, new_heading)

            env.sims[agent_id].set_property_value(c.steady_flight, env.sims[agent_id].get_property_value(c.steady_flight) + self.steady_time)

        if done:
            print(f'INFO: agent[{agent_id}] unreached heading!')
            info[f'agent{agent_id}_end_reason'] = 1  # crash
        success = False
        return done, success, info
