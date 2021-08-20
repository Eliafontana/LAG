from envs.JSBSim.core.render_tacview import data_replay
import pdb
import time
import numpy as np
from envs.env_wrappers import SubprocVecEnv, DummyVecEnv
from envs.JSBSim.envs import SingleCombatEnv, SingleControlEnv
from envs.env_wrappers import SubprocVecEnv, DummyVecEnv
from envs.JSBSim.core.catalog import Catalog as c


def test_env():
    # env = SingleCombatEnv(config_name='singlecombat_selfplay')
    env = SingleCombatEnv(config_name='singlecombat_vsbaseline')
    act_space = env.action_space[0]

    env.reset()
    cur_step = -1
    episode_reward = np.zeros(env.num_agents)
    start_time = time.time()
    while True:
        cur_step += 1
        # flying straight forward
        actions = [np.array([20, 18.6, 20, 0]) for _ in range(env.num_agents)]
        # random fly
        # actions = [act_space.sample() for _ in range(env.num_agents)]
        next_obs, reward, done, env_info = env.step(actions)
        episode_reward += reward
        print(episode_reward)
        if np.array(done).all():
            print(env_info)
            break
    print(time.time() - start_time)


def test_parallel_env():
    
    def make_train_env(num_env, config_name='singlecombat_vsbaseline'):
        def env_fn():
            return SingleCombatEnv(config_name=config_name)
        return DummyVecEnv([env_fn for _ in range(num_env)])

    start_time = time.time()
    num_env = 2
    envs = make_train_env(num_env)
    act_space = envs.action_space[0]
    num_agents = len(envs.action_space)

    n_total_steps = 2000
    n_current_steps = 0
    n_current_episodes = 0
    obss = envs.reset()
    while n_current_steps < n_total_steps:
        actions = [[act_space.sample() for _ in range(num_agents)] for _ in range(num_env)]
        next_obss, rewards, dones, env_infos = envs.step(actions)
        new_samples = list(zip(obss, actions, rewards, next_obss, dones))
        n_current_steps += len(new_samples)
        for i, done in enumerate(dones):
            if np.array(done).all():
                n_current_episodes += 1
    print(f"Collect data finish: total step {n_current_steps}, total episode {n_current_episodes}, timecost: {time.time() - start_time:.2f}s")
    envs.close()

def test_heading_env():
    env = SingleControlEnv(config_name='heading_altitude_task')
    # env = SelfPlayEnv(config='selfplay_with_missile_task')
    act_space = env.action_space
    trajectory_list = []
    env.reset()
    trajectory_list.append(env.render())
    reward_render = {}
    cur_step = 0
    reward_blue = 0
    start_time = time.time()
    while True:
        cur_step += 1
        # flying straight forward
        actions = [np.array([20., 18.6, 20., 0.])]
        # random fly
        # actions = act_space.sample()
        next_obs, reward, done, env_info = env.step(actions)
        trajectory_list.append(env.render())
        # print(reward_blue)
        if done:
            print(env_info)
            #reward_render = env.task.reward_functions[0].get_reward_trajectory()
            print(env.sims[0].get_property_value(c.simulation_sim_time_sec))
            break
    print(time.time() - start_time)
    #print(reward_render)
    np.save('save_trajectories.npy', np.asarray(trajectory_list))

    
# test_env()
# test_parallel_env()
test_heading_env()
