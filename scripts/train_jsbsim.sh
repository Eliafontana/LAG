#!/bin/sh
env="SingleControl"
scenario="single/heading"
num_agents=1
algo="ppo"
exp="no_act_hidden"
seed=3

echo "env is ${env}, scenario is ${scenario}, algo is ${algo}, exp is ${exp}, seed is ${seed}"
CUDA_VISIBLE_DEVICES=0 python train/train_jsbsim.py \
    --env-name ${env} --algorithm-name ${algo} --scenario-name ${scenario} --experiment-name ${exp} --num-agents ${num_agents} \
    --user-name 'jyh' --use-wandb --wandb-name 'jyh' \
    --seed ${seed} --n-training-threads 1 --n-rollout-threads 32 --cuda\
    --num-mini-batch 5 --buffer-size 2700 --episode-length 900 --num-env-steps 1e8 \
    --lr 3e-4 --gamma 0.99 --ppo-epoch 4 --clip-params 0.2 --max-grad-norm 2 --entropy-coef 1e-3 \
    --hidden-size "128 128" --act-hidden-size "" \
    --recurrent-hidden-size 128 --recurrent-hidden-layers 1 --data-chunk-length 8 \
    --log-interval 1 --save-interval 100 \