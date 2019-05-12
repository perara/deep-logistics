import time
from collections import OrderedDict
import gym
from absl import flags
import tensorflow as tf
import datetime
import os
import numpy as np

from experiments.experiment_5.per_rl import utils
from experiments.experiment_5.per_rl.storage.batch_handler import DynamicBatch
from experiments.experiment_5.per_rl.utils.metrics import Metrics

FLAGS = flags.FLAGS


class Agent:
    SUPPORTED_BATCH_MODES = ["episodic", "steps"]
    DEFAULTS = dict()
    arguments = utils.arguments

    def __init__(self,
                 obs_space: gym.spaces.Box,
                 action_space: gym.spaces.Discrete,
                 policies: dict,
                 policy_update=dict(
                     interval=5,  # Update every 5 training epochs,
                     strategy="copy",  # "copy, mean"
                 ),
                 batch_shuffle=False,
                 batch_mode: str = "episodic",
                 mini_batches: int = 1,
                 batch_size: int = 32,
                 epochs: int = 1,
                 grad_clipping=None,
                 dtype=tf.float32,
                 tensorboard_enabled=False,
                 tensorboard_path="./tb/",
                 name_prefix=None,
                 inference_only=False):
        hyper_parameters = utils.get_defaults(self, Agent.arguments())

        self.name = self.__class__.__name__

        if tensorboard_enabled:
            logdir = os.path.join(
                tensorboard_path, "%s_%s" %
                                  (
                                      self.name + name_prefix if name_prefix else self.name,
                                      datetime.datetime.now().strftime("%m_%d_%Y_%H_%M_%S"))
            )
            writer = tf.summary.create_file_writer(logdir)
            writer.set_as_default()

        """Define properties."""
        self.obs_space = obs_space
        self.action_space = action_space
        self.policies = policies
        self.batch_mode = batch_mode
        self.batch_size = batch_size
        self.batch_shuffle = batch_shuffle
        self.mini_batches = mini_batches
        self.dtype = dtype
        self.grad_clipping = grad_clipping
        self.inference_only = inference_only
        self.epochs = epochs

        self._tensorboard_enabled = tensorboard_enabled
        self._tensorboard_path = tensorboard_path
        self._name_prefix = name_prefix

        self.metrics = Metrics(self)
        self.data = dict()  # Keeps track of all data per iteration. Resets after train()
        self.losses = dict()
        self.operations = OrderedDict()

        self.metrics.text("hyperparameters", tf.convert_to_tensor(utils.hyperparameters_to_table(hyper_parameters)))

        self.policies = {
            k: v(self) for k, v in policies.items()
        }

        if batch_mode not in Agent.SUPPORTED_BATCH_MODES:
            raise NotImplementedError("The batch mode %s is not supported. Use one of the following: %s" %
                                      (batch_mode, Agent.SUPPORTED_BATCH_MODES))
        self.batch_mode = batch_mode
        """Find all policies with inference flag set. Ensure that its only 1 and assign as the inference 
        policy. """
        self.inference_policy = [x for k, x in self.policies.items() if x.inference]
        if len(self.inference_policy) != 1:
            raise ValueError("There can only be 1 policy with the flag training=False.")
        self.inference_policy = self.inference_policy[0]

        """This list contains names of policies that should be trained"""
        self.training_policies = [(k, x) for k, x in self.policies.items() if x.training]

        # Policy update. This is the strategy used when using multiple policies (ie one trainer and one predictor)
        # Settings here determine how updates should be performed.
        self.policy_update = policy_update
        self.policy_update_counter = 0
        self.policy_update_frequency = self.policy_update["interval"]
        self.policy_update_enabled = len(self.policies) > 1

        self.batch = DynamicBatch(
            agent=self,
            obs_space=obs_space,
            action_space=action_space,
            batch_size=batch_size
        )

        self.obs = None  # Last seen observation

    def add_operation(self, name, fn):
        self.operations[name] = fn

    def remove_calculation(self, name):
        del self.operations[name]

    def add_loss(self, name, lambda_fn, tb_text=None):
        self.losses[name] = lambda_fn

        if tb_text:
            """Add text on tensorboard"""
            self.metrics.text(name, tb_text)

    def remove_loss(self, name):
        del self.losses[name]

    # @tf.function
    def predict(self, inputs):
        """
        :param inputs: DATA INPUT
        :param policy: Which policy to use. When None, self.inference_policy will be used.
        :return:
        """
        if inputs.ndim == 1:
            inputs = inputs[None, :]
        self.data["inputs"] = inputs

        pred = self.inference_policy(inputs)
        self.data.update(pred)

        return pred

    def observe(self, **kwargs):
        """
        Observe the resulting transition
        :param obs1: The next state s_t+1
        :param reward: Reward yielded back from R_t = S_t(a_t)
        :param terminal: T_t = S_t(a_t) (Not really RL thing, but we keep track of Terminal states
        modify reward in some cases.
        :return:
        """
        self.data.update(**kwargs)

        """Metrics update."""
        self.metrics.add("steps", 1, ["sum_episode", "sum_mean_total"], "summary")
        self.metrics.add("reward", kwargs["reward"], ["sum_episode", "sum_mean_frequent", "sum_mean_total"], "summary")

        if kwargs["terminal"]:
            self.metrics.summarize()

        ready = self.batch.add(
            **self.data
        )

        if ready:  # or not self.inference_only:
            train_start = time.perf_counter()
            losses = self.train()

            """Update metrics for training"""
            self.metrics.add("total", np.mean(losses), ["sum_mean_frequent", "mean_total"], "loss")
            self.metrics.add("training_time", time.perf_counter() - train_start, ["mean_total"], "time")
            self.metrics.add("iteration_per_episode", 1, ["sum_episode"], "time/training")

    def _backprop(self, name, policy, **kwargs):
        total_loss = 0
        losses = []

        """Run all loss functions"""
        with tf.GradientTape() as tape:

            pred = policy(**kwargs)
            kwargs.update(pred)

            for loss_name, loss_fn in self.losses.items():
                loss = loss_fn(**kwargs)

                """Add metric for loss"""
                self.metrics.add(loss_name + "/" + name, loss, ["mean_total"], "loss")

                """Add to total loss"""
                total_loss += loss
                losses.append(loss)

        """Calculate gradients"""
        grads = tape.gradient(total_loss, policy.trainable_variables)

        """Gradient Clipping"""
        if self.grad_clipping is not None:
            grads, _grad_norm = tf.clip_by_global_norm(grads, self.grad_clipping)

        """Diagnostics"""
        self.metrics.add("variance", np.mean([np.var(grad) for grad in grads]), ["sum_mean_frequent"], "gradients")
        self.metrics.add("l2", np.mean([np.sqrt(np.mean(np.square(grad))) for grad in grads]), ["sum_mean_frequent"],
                         "gradients")

        """Backprop"""
        policy.optimizer.apply_gradients(zip(grads, policy.trainable_variables))

        """Record learning rate"""
        self.metrics.add("lr/" + name, policy.optimizer.lr.numpy(), ["mean_total"], "hyper-parameter")

        """Policy update strategy (If applicable)."""
        if self.policy_update_enabled and self.policy_update_counter % self.policy_update_frequency == 0:
            strategy = self.policy_update["strategy"]

            if strategy == "mean":
                raise NotImplementedError("Not implemented yet")
            elif strategy == "copy":
                for name, policy in self.training_policies:
                    self.inference_policy.set_weights(policy.get_weights())
                self.policy_update_counter = 0
            else:
                raise NotImplementedError(
                    "The policy update strategy %s is not implemented for the BaseAgent." % strategy)

        return np.asarray(losses)

    def train(self, **kwargs):
        # For each policy
        for name, policy in self.training_policies:

            # Pack policy into the data stream
            kwargs["policy"] = policy
            kwargs["name"] = name

            # Retrieve batch of data
            batch = self.batch.get()

            # Perform calculations prior to training
            for opname, operation in self.operations.items():
                return_op = operation(**batch, **kwargs)

                if isinstance(return_op, dict):
                    for k, v in return_op.items():
                        batch[k] = v
                else:
                    batch[opname] = return_op

            batch_indices = np.arange(self.batch.counter)  # We use counter because episodic wil vary in bsize.
            # Calculate mini-batch size
            for epoch in range(self.epochs):

                # Shuffle the batch indices
                if self.batch_shuffle:
                    np.random.shuffle(batch_indices)

                for i in range(0, self.batch.counter, self.batch.mbsize):
                    # Sample indices for the mini-batch
                    mb_indexes = batch_indices[i:i + self.batch.mbsize]

                    # Cast all elements to numpy arrays
                    mb = {k: np.asarray(v)[mb_indexes] for k, v in batch.items()}

                    losses = self._backprop(**mb, **kwargs)

                self.metrics.add("epochs", 1, ["sum_total"], "time/training")

            self.policy_update_counter += 1
            self.batch.done()
            return losses
