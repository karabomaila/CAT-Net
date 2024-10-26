#!/bin/bash
# train a model to segment abdominal MRI (T2 fold of CHAOS challenge)
GPUID1=0
export CUDA_VISIBLE_DEVICES=$GPUID1

###### Shared configs ######
DATASET='AMOS'
NWORKER=0
RUNS=1
ALL_EV=(2) # 5-fold cross validation (0,  1,  2,  3,  5,  6,  7,  8,  9, 10, 11, 12, 13)
TEST_LABEL=(10, 14)
EXCLUDE_LABEL=None
USE_GT=True
###### Training configs ######
NSTEP=100000
DECAY=0.98

MAX_ITER=1000 # defines the size of an epoch
SNAPSHOT_INTERVAL=100 # interval for saving snapshot
SEED=2021

echo ========================================================================

for EVAL_FOLD in "${ALL_EV[@]}"
do
  PREFIX="train_${DATASET}_cv${EVAL_FOLD}"
  echo $PREFIX
  LOGDIR="./exps_on_${DATASET}"

  if [ ! -d $LOGDIR ]
  then
    mkdir -p $LOGDIR
  fi
  pwd
  .venv/bin/python  train_main.py with \
  mode='train' \
  dataset=$DATASET \
  num_workers=$NWORKER \
  n_steps=$NSTEP \
  eval_fold=$EVAL_FOLD \
  test_label=$TEST_LABEL \
  exclude_label=$EXCLUDE_LABEL \
  use_gt=$USE_GT \
  max_iters_per_load=$MAX_ITER \
  seed=$SEED \
  save_snapshot_every=$SNAPSHOT_INTERVAL \
  lr_step_gamma=$DECAY \
  path.log_dir=$LOGDIR
done
