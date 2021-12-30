import time
import torch
import logging
import numpy as np
from typing import List
from collections import defaultdict
from .base_runner import Runner, ReplayBuffer


def _t2n(x):
    return x.detach().cpu().numpy()


class JSBSimRunner(Runner):

    def load(self):
        self.obs_space = self.envs.observation_space
        self.act_space = self.envs.action_space
        self.num_agents = self.envs.num_agents
        self.use_selfplay = self.all_args.use_selfplay  # type: bool

        # policy & algorithm
        if self.algorithm_name == "ppo":
            from algorithms.ppo.ppo_trainer import PPOTrainer as Trainer
            from algorithms.ppo.ppo_policy import PPOPolicy as Policy
        else:
            raise NotImplementedError
        self.policy = Policy(self.all_args, self.obs_space, self.act_space, device=self.device)
        self.trainer = Trainer(self.all_args, device=self.device)

        # buffer
        if self.num_agents > 1 and self.use_selfplay:
            self.buffer = ReplayBuffer(self.all_args, self.num_agents // 2, self.obs_space, self.act_space)
        else:
            self.buffer = ReplayBuffer(self.all_args, self.num_agents, self.obs_space, self.act_space)

        # [Selfplay] allocate memory for opponent policy/data in training
        if self.use_selfplay:
            assert self.all_args.n_choose_opponents <= self.n_rollout_threads, \
                "Number of different opponents({}) must less than or equal to number of training threads({})!" \
                .format(self.all_args.n_choose_opponents, self.n_rollout_threads)
            self.policy_pool = defaultdict(float)
            self.opponent_policy = [
                Policy(self.all_args, self.obs_space, self.act_space, device=self.device)
                for _ in range(self.all_args.n_choose_opponents)]
            self.opponent_env_split = np.array_split(np.arange(self.n_rollout_threads), len(self.opponent_policy))
            self.opponent_obs = np.zeros_like(self.buffer.obs[0])
            self.opponent_rnn_states = np.zeros_like(self.buffer.rnn_states_actor[0])
            self.opponent_masks = np.ones_like(self.buffer.masks[0])
            if self.use_eval:
                assert self.all_args.n_choose_opponents <= self.n_eval_rollout_threads, \
                    "Number of different opponents({}) must less than or equal to number of eval threads({})!" \
                    .format(self.all_args.n_choose_opponents, self.n_eval_rollout_threads)
                self.eval_opponent_policy = [
                    Policy(self.all_args, self.obs_space, self.act_space, device=self.device)
                    for _ in range(self.all_args.n_choose_opponents)]

        # if self.model_dir is not None:
        #     self.restore()

    def run(self):
        self.warmup()

        start = time.time()
        self.total_num_steps = 0
        episodes = self.num_env_steps // self.buffer_size // self.n_rollout_threads

        for episode in range(episodes):

            heading_turns_list = []

            for step in range(self.buffer_size):
                # Sample actions
                values, actions, action_log_probs, rnn_states_actor, rnn_states_critic = self.collect(step)

                # Obser reward and next obs
                obs, rewards, dones, infos = self.envs.step(actions)

                data = obs, actions, rewards, dones, action_log_probs, values, rnn_states_actor, rnn_states_critic

                # insert data into buffer
                self.insert(data)

                # Extra recorded information
                for info in infos:
                    if 'heading_turn_counts' in info:
                        heading_turns_list.append(info['heading_turn_counts'])

            # compute return and update network
            self.compute()
            train_infos = self.train()

            # post process
            self.total_num_steps = (episode + 1) * self.buffer_size * self.n_rollout_threads

            # save model
            if (episode % self.save_interval == 0) or (episode == episodes - 1):
                self.save(episode)

            # log information
            if episode % self.log_interval == 0:
                end = time.time()
                print("\n Scenario {} Algo {} Exp {} updates {}/{} episodes, total num timesteps {}/{}, FPS {}.\n"
                      .format(self.all_args.scenario_name,
                              self.algorithm_name,
                              self.experiment_name,
                              episode,
                              episodes,
                              self.total_num_steps,
                              self.num_env_steps,
                              int(self.total_num_steps / (end - start))))

                train_infos["average_episode_rewards"] = self.buffer.rewards.sum() / (self.buffer.masks == False).sum()
                print("average episode rewards is {}".format(train_infos["average_episode_rewards"]))

                if len(heading_turns_list):
                    train_infos["average_heading_turns"] = np.mean(heading_turns_list)
                    print("average heading turns is {}".format(train_infos["average_heading_turns"]))
                self.log_info(train_infos, self.total_num_steps)

            # eval
            if episode % self.eval_interval == 0 and self.use_eval:
                self.eval(self.total_num_steps)
                # reset opponent
                if self.use_selfplay:
                    self.reset_opponent()

    def warmup(self):
        # reset env
        obs = self.envs.reset()
        # [Selfplay] divide ego/opponent of initial obs
        if self.use_selfplay:
            self.opponent_obs = obs[:, self.num_agents // 2:, ...]
            obs = obs[:, :self.num_agents // 2, ...]
        self.buffer.step = 0
        # self.buffer.obs[0] = obs.copy()

    @torch.no_grad()
    def collect(self, step):
        self.policy.prep_rollout()
        values, actions, action_log_probs, rnn_states_actor, rnn_states_critic \
            = self.policy.get_actions(np.concatenate(self.buffer.obs[step]),
                                      np.concatenate(self.buffer.rnn_states_actor[step]),
                                      np.concatenate(self.buffer.rnn_states_critic[step]),
                                      np.concatenate(self.buffer.masks[step]))
        # split parallel data [N*M, shape] => [N, M, shape]
        values = np.array(np.split(_t2n(values), self.n_rollout_threads))
        actions = np.array(np.split(_t2n(actions), self.n_rollout_threads))
        action_log_probs = np.array(np.split(_t2n(action_log_probs), self.n_rollout_threads))
        rnn_states_actor = np.array(np.split(_t2n(rnn_states_actor), self.n_rollout_threads))
        rnn_states_critic = np.array(np.split(_t2n(rnn_states_critic), self.n_rollout_threads))

        # [Selfplay] get actions of opponent policy
        if self.use_selfplay:
            opponent_actions = np.zeros_like(actions)
            for policy_idx, policy in enumerate(self.opponent_policy):
                env_idx = self.opponent_env_split[policy_idx]
                opponent_action, opponent_rnn_states \
                    = policy.act(np.concatenate(self.opponent_obs[env_idx]),
                                 np.concatenate(self.opponent_rnn_states[env_idx]),
                                 np.concatenate(self.opponent_masks[env_idx]))
                opponent_actions[env_idx] = np.array(np.split(_t2n(opponent_action), len(env_idx)))
                self.opponent_rnn_states[env_idx] = np.array(np.split(_t2n(opponent_rnn_states), len(env_idx)))
            actions = np.concatenate((actions, opponent_actions), axis=1)

        return values, actions, action_log_probs, rnn_states_actor, rnn_states_critic

    def insert(self, data: List[np.ndarray]):
        obs, actions, rewards, dones, action_log_probs, values, rnn_states_actor, rnn_states_critic = data

        dones_env = np.all(dones.squeeze(axis=-1), axis=-1)

        rnn_states_actor[dones_env == True] = np.zeros(((dones_env == True).sum(), *rnn_states_actor.shape[1:]), dtype=np.float32)
        rnn_states_critic[dones_env == True] = np.zeros(((dones_env == True).sum(), *rnn_states_critic.shape[1:]), dtype=np.float32)

        masks = np.ones((self.n_rollout_threads, self.num_agents, 1), dtype=np.float32)
        masks[dones_env == True] = np.zeros(((dones_env == True).sum(), self.num_agents, 1), dtype=np.float32)

        # [Selfplay] divide ego/opponent of collecting data
        if self.use_selfplay:
            self.opponent_obs = obs[:, self.num_agents // 2:, ...]
            self.opponent_masks = masks[:, self.num_agents // 2:, ...]

            obs = obs[:, :self.num_agents // 2, ...]
            actions = actions[:, :self.num_agents // 2, ...]
            rewards = rewards[:, :self.num_agents // 2, ...]
            masks = masks[:, :self.num_agents // 2, ...]

        self.buffer.insert(obs, actions, rewards, masks, action_log_probs, values, rnn_states_actor, rnn_states_critic)

    @torch.no_grad()
    def eval(self, total_num_steps):
        total_episodes, eval_episode_rewards = 0, []
        eval_cumulative_rewards = np.zeros((self.n_eval_rollout_threads, *self.buffer.rewards.shape[2:]), dtype=np.float32)

        eval_obs = self.eval_envs.reset()
        eval_masks = np.ones((self.n_eval_rollout_threads, *self.buffer.masks.shape[2:]), dtype=np.float32)
        eval_rnn_states = np.zeros((self.n_eval_rollout_threads, *self.buffer.rnn_states_actor.shape[2:]), dtype=np.float32)

        # [Selfplay] divide ego/opponent of collecting data
        if self.use_selfplay:
            eval_opponent_obs = eval_obs[:, self.num_agents // 2:, ...]
            eval_obs = eval_obs[:, :self.num_agents // 2, ...]
            eval_opponent_masks = np.ones_like(eval_masks, dtype=np.float32)
            eval_opponent_rnn_states = np.zeros_like(eval_rnn_states, dtype=np.float32)
            eval_opponent_env_split = np.array_split(np.arange(self.n_eval_rollout_threads), len(self.opponent_policy))

        while total_episodes < self.eval_episodes:
            self.policy.prep_rollout()
            eval_actions, eval_rnn_states = self.policy.act(np.concatenate(eval_obs),
                                                            np.concatenate(eval_rnn_states),
                                                            np.concatenate(eval_masks), deterministic=True)
            eval_actions = np.array(np.split(_t2n(eval_actions), self.n_eval_rollout_threads))
            eval_rnn_states = np.array(np.split(_t2n(eval_rnn_states), self.n_eval_rollout_threads))

            # [Selfplay] get actions of opponent policy
            if self.use_selfplay:
                eval_opponent_actions = np.zeros_like(eval_actions)
                for policy_idx, policy in enumerate(self.opponent_policy):
                    env_idx = eval_opponent_env_split[policy_idx]
                    eval_opponent_action, eval_opponent_rnn_state \
                        = policy.act(np.concatenate(eval_opponent_obs[env_idx]),
                                     np.concatenate(eval_opponent_rnn_states[env_idx]),
                                     np.concatenate(eval_opponent_masks[env_idx]))
                    eval_opponent_rnn_states[env_idx] = np.array(np.split(_t2n(eval_opponent_rnn_state), len(env_idx)))
                    eval_opponent_actions[env_idx] = np.array(np.split(_t2n(eval_opponent_action), len(env_idx)))
                eval_actions = np.concatenate((eval_actions, eval_opponent_actions), axis=1)

            # Obser reward and next obs
            eval_obs, eval_rewards, eval_dones, eval_infos = self.eval_envs.step(eval_actions)

            # [Selfplay] get ego reward
            eval_rewards = eval_rewards[:, :self.num_agents // 2, ...] if self.use_selfplay else eval_rewards
            eval_cumulative_rewards += eval_rewards

            eval_dones_env = np.all(eval_dones.squeeze(axis=-1), axis=-1)
            total_episodes += np.sum(eval_dones_env)
            eval_episode_rewards.append(eval_cumulative_rewards[eval_dones_env == True])
            eval_cumulative_rewards[eval_dones_env == True] = 0

            eval_masks = np.ones_like(eval_masks, dtype=np.float32)
            eval_masks[eval_dones_env == True] = np.zeros(((eval_dones_env == True).sum(), *eval_masks.shape[1:]), dtype=np.float32)
            eval_rnn_states[eval_dones_env == True] = np.zeros(((eval_dones_env == True).sum(), *eval_rnn_states.shape[1:]), dtype=np.float32)
            # [Selfplay] reset opponent mask/rnn_states
            if self.use_selfplay:
                eval_opponent_obs = eval_obs[:, self.num_agents // 2:, ...]
                eval_obs = eval_obs[:, :self.num_agents // 2, ...]
                eval_opponent_masks[eval_dones_env == True] = \
                    np.zeros(((eval_dones_env == True).sum(), *eval_opponent_masks.shape[1:]), dtype=np.float32)
                eval_opponent_rnn_states[eval_dones_env == True] = \
                    np.zeros(((eval_dones_env == True).sum(), *eval_opponent_rnn_states.shape[1:]), dtype=np.float32)

        eval_infos = {}
        eval_infos['eval_average_episode_rewards'] = np.concatenate(eval_episode_rewards).mean(axis=1)
        print("\neval average episode rewards: " + str(np.mean(eval_infos['eval_average_episode_rewards'])))
        self.log_info(eval_infos, total_num_steps)

    @torch.no_grad()
    def render(self):
        assert self.n_render_rollout_threads == 1, 'JSBSim Env only support n_render_rollout_threads = 1'
        render_episode_rewards = 0
        render_obs = self.envs.reset()
        render_masks = np.ones((1, *self.buffer.masks.shape[2:]), dtype=np.float32)
        render_rnn_states = np.zeros((1, *self.buffer.rnn_states_actor.shape[2:]), dtype=np.float32)
        self.envs.render(mode='txt', filepath=f'{self.run_dir}/{self.experiment_name}.txt.acmi')
        while True:
            # self.policy.prep_rollout()
            # render_actions, render_rnn_states = self.policy.act(np.concatenate(render_obs),
            #                                                             np.concatenate(render_rnn_states),
            #                                                             np.concatenate(render_masks),
            #                                                             deterministic=True)
            # render_actions = np.expand_dims(_t2n(render_actions), axis=0)
            # render_rnn_states = np.expand_dims(_t2n(render_rnn_states), axis=0)

            # # Obser reward and next obs
            render_obs, render_rewards, render_dones, render_infos = self.envs.step([None])
            # render_episode_rewards += render_rewards
            self.envs.render(mode='txt', filepath=f'{self.run_dir}/{self.experiment_name}.txt.acmi')
            # if render_dones.any():
            #     break

        render_infos = {}
        render_infos['render_episode_reward'] = render_episode_rewards
        print("render episode reward of agent: " + str(render_infos['render_episode_reward']))

    def save(self, episode):
        policy_actor_state_dict = self.policy.actor.state_dict()
        torch.save(policy_actor_state_dict, str(self.save_dir) + '/actor.pt')
        policy_critic_state_dict = self.policy.critic.state_dict()
        torch.save(policy_critic_state_dict, str(self.save_dir) + '/critic.pt')
        # [Selfplay] save policy & performance
        if self.use_selfplay:
            torch.save(policy_actor_state_dict, str(self.save_dir) + f'/actor_{episode}.pt')
            self.policy_pool[episode] = self.all_args.init_elo

    def reset_opponent(self):
        # selfplay
        if self.all_args.selfplay_algorithm == "sp":
            for policy in self.opponent_policy:
                policy_actor_state_dict = torch.load(str(self.save_dir) + '/actor.pt')
                policy.actor.load_state_dict(policy_actor_state_dict)
                policy.prep_rollout()
                logging.info("Load the latest policy")
        # fictious selfplay
        elif self.all_args.selfplay_algorithm == "fsp":
            for policy in self.opponent_policy:
                random_idx = np.random.choice(list(self.policy_pool.keys()))
                policy_actor_state_dict = torch.load(str(self.save_dir) + f'/actor_{random_idx}.pt')
                policy.actor.load_state_dict(policy_actor_state_dict)
                policy.prep_rollout()
                logging.info(f"Load history(iter={random_idx}) policy")
        else:
            raise NotImplementedError("Unknown algorithm {}".format(self.all_args.selfplay_algorithm))

        # clear buffer
        self.buffer.clear()
        self.opponent_obs = np.zeros_like(self.opponent_obs)
        self.opponent_rnn_states = np.zeros_like(self.opponent_rnn_states)
        self.opponent_masks = np.ones_like(self.opponent_masks)

        # reset env
        obs = self.envs.reset()
        if self.all_args.n_choose_opponents > 0:
            self.opponent_obs = obs[:, self.num_agents // 2:, ...]
            obs = obs[:, :self.num_agents // 2, ...]
        self.buffer.obs[0] = obs.copy()
