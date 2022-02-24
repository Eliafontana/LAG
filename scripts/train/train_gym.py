#!/usr/bin/env python
import imp
import sys
import os
from typing import Tuple
import gym
import wandb
import socket
import torch
import random
import numpy as np
from pathlib import Path
import logging
import setproctitle
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__)))))
from config import get_config
from runner.gym_runner import GymRunner as Runner
from envs import gym_compete
from envs.env_wrappers import SubprocVecEnv, DummyVecEnv


class GymEnv:
    def __init__(self, env):
        self.env = env
        self.num_agents = self.env.num_agents
        self.action_space = self.env.action_space[0] if isinstance(self.env.action_space, gym.spaces.Tuple) \
                                                        else self.env.action_space 
        self.observation_space = self.env.observation_space[0] if isinstance(self.env.observation_space, gym.spaces.Tuple) \
                                                        else self.env.observation_space 
        self.obs_shape = (self.num_agents, *self.observation_space.shape)
        self.act_shape = (self.num_agents, *self.action_space.shape)
        self.rew_shape = (self.num_agents, 1)
        self.done_shape = (self.num_agents, 1)

    def reset(self):
        observation = self.env.reset()
        return np.array(observation).reshape(self.obs_shape)

    def step(self, action):
        action = np.array(action).reshape(self.act_shape)
        observation, reward, done, info = self.env.step(action)
        observation = np.array(observation).reshape(self.obs_shape)
        done = np.array(done).reshape(self.done_shape)
        reward = np.array(reward).reshape(self.rew_shape)
        return observation, reward, done, info

    def render(self, mode="human"):
        self.env.render(mode)

    def close(self):
        self.env.close()


def make_train_env(all_args):
    def get_env_fn(rank):
        def init_env():
            env = gym.make(all_args.scenario_name)
            # env.seed(all_args.seed + rank * 1000)
            return GymEnv(env)
        return init_env
    if all_args.n_rollout_threads == 1:
        return DummyVecEnv([get_env_fn(0)])
    else:
        return SubprocVecEnv([get_env_fn(i) for i in range(all_args.n_rollout_threads)])


def parse_args(args, parser):
    group = parser.add_argument_group("Gym Env parameters")
    group.add_argument('--scenario-name', type=str, default='CartPole-v1',
                       help="the name of gym env")
    group.add_argument('--episode-length', type=int, default=1000,
                       help="the max length of an episode")
    group.add_argument('--num-agents', type=int, default=1,
                       help="number of agents controlled by RL policy")
    all_args = parser.parse_known_args(args)[0]
    return all_args


def main(args):
    parser = get_config()
    all_args = parse_args(args, parser)

    # seed
    np.random.seed(all_args.seed)
    random.seed(all_args.seed)
    torch.manual_seed(all_args.seed)
    torch.cuda.manual_seed_all(all_args.seed)

    # cuda
    if all_args.cuda and torch.cuda.is_available():
        logging.info("choose to use gpu...")
        device = torch.device("cuda:0")  # use cude mask to control using which GPU
        torch.set_num_threads(all_args.n_training_threads)
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = True
    else:
        logging.info("choose to use cpu...")
        device = torch.device("cpu")
        torch.set_num_threads(all_args.n_training_threads)

    # run dir
    run_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/results") \
        / all_args.env_name / all_args.scenario_name / all_args.algorithm_name / all_args.experiment_name
    if not run_dir.exists():
        os.makedirs(str(run_dir))

    # wandb
    if all_args.use_wandb:
        run = wandb.init(config=all_args,
                         project=all_args.env_name,
                         entity=all_args.wandb_name,
                         notes=socket.gethostname(),
                         name=f"{all_args.algorithm_name}_{all_args.experiment_name}_seed{all_args.seed}",
                         group=all_args.scenario_name,
                         dir=str(run_dir),
                         job_type="training",
                         reinit=True)
    else:
        if not run_dir.exists():
            curr_run = 'run1'
        else:
            exst_run_nums = [int(str(folder.name).split('run')[1]) for folder in run_dir.iterdir() if str(folder.name).startswith('run')]
            if len(exst_run_nums) == 0:
                curr_run = 'run1'
            else:
                curr_run = 'run%i' % (max(exst_run_nums) + 1)
        run_dir = run_dir / curr_run
        if not run_dir.exists():
            os.makedirs(str(run_dir))

    setproctitle.setproctitle(str(all_args.algorithm_name) + "-" + str(all_args.env_name)
                              + "-" + str(all_args.experiment_name) + "@" + str(all_args.user_name))

    # env init
    envs = make_train_env(all_args)
    num_agents = all_args.num_agents

    config = {
        "all_args": all_args,
        "envs": envs,
        "eval_envs": None,
        "device": device,
        "num_agents": num_agents,
        "run_dir": run_dir
    }

    # run experiments
    runner = Runner(config)
    runner.run()

    # post process
    envs.close()

    if all_args.use_wandb:
        run.finish()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main(sys.argv[1:])
