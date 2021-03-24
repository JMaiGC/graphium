from typing import Dict, List, Any, Union, Any, Callable, Tuple, Type, Optional
import os, math
import dgl
import numpy as np
import pandas as pd
from copy import deepcopy
import yaml
from omegaconf import DictConfig, ListConfig

import torch
import torch.nn.functional as F
from torch import nn
from torch.optim.lr_scheduler import ReduceLROnPlateau

import pytorch_lightning as pl
from pytorch_lightning import _logger as log
from pytorch_lightning.utilities.exceptions import MisconfigurationException

from goli.trainer.model_summary import ModelSummaryExtended


LOSS_DICT = {
    "mse": torch.nn.MSELoss(),
    "bce": torch.nn.BCELoss(),
    "l1": torch.nn.L1Loss(),
    "mae": torch.nn.L1Loss(),
    "cosine": torch.nn.CosineEmbeddingLoss(),
}


class EpochSummary:
    r"""Container for collecting epoch-wise results"""

    def __init__(self, monitor="loss", monitor_greater: bool = False, metrics_on_progress_bar=[]):
        self.monitor = monitor
        self.monitor_greater = monitor_greater
        self.metrics_on_progress_bar = metrics_on_progress_bar
        self.summaries = {}
        self.best_summaries = {}

    class Results:
        def __init__(
            self,
            targets: torch.Tensor,
            predictions: torch.Tensor,
            loss: float,
            metrics: dict,
            monitored_metric: str,
            n_epochs: int,
        ):
            self.targets = targets
            self.predictions = predictions
            self.loss = loss
            self.monitored_metric = monitored_metric
            self.monitored = metrics[monitored_metric]
            self.metrics = {key: value.tolist() for key, value in metrics.items()}
            self.n_epochs = n_epochs

    def set_results(self, name, targets, predictions, loss, metrics, n_epochs) -> float:
        metrics[f"loss/{name}"] = loss
        self.summaries[name] = EpochSummary.Results(
            targets=targets,
            predictions=predictions,
            loss=loss,
            metrics=metrics,
            monitored_metric=f"{self.monitor}/{name}",
            n_epochs=n_epochs,
        )
        if self.is_best_epoch(name, loss, metrics):
            self.best_summaries[name] = self.summaries[name]

    def is_best_epoch(self, name, loss, metrics):
        if not (name in self.best_summaries.keys()):
            return True

        metrics[f"loss/{name}"] = loss
        monitor_name = f"{self.monitor}/{name}"
        return (self.monitor_greater and (metrics[monitor_name] > self.best_summaries[name].monitored)) or (
            (not self.monitor_greater) and (metrics[monitor_name] < self.best_summaries[name].monitored)
        )

    def get_results(self, name):
        return self.summaries[name]

    def get_best_results(self, name):
        return self.best_summaries[name]

    def get_results_on_progress_bar(self, name):
        results = self.summaries[name]
        results_prog = {
            f"{kk}/{name}": results.metrics[f"{kk}/{name}"] for kk in self.metrics_on_progress_bar
        }
        return results_prog

    def get_dict_summary(self):
        full_dict = {}
        # Get metric summaries
        full_dict["metric_summaries"] = {}
        for key, val in self.summaries.items():
            full_dict["metric_summaries"][key] = {k: v for k, v in val.metrics.items()}
            full_dict["metric_summaries"][key]["n_epochs"] = val.n_epochs

        # Get metric summaries at best epoch
        full_dict["best_epoch_metric_summaries"] = {}
        for key, val in self.best_summaries.items():
            full_dict["best_epoch_metric_summaries"][key] = val.metrics
            full_dict["best_epoch_metric_summaries"][key]["n_epochs"] = val.n_epochs

        return full_dict


class PredictorModule(pl.LightningModule):
    def __init__(
        self,
        model_class: Type[nn.Module],
        model_kwargs: Dict[str, Any],
        loss_fun: Union[str, Callable],
        random_seed: int = 42,
        optim_kwargs: Optional[Dict[str, Any]] = None,
        lr_reduce_on_plateau_kwargs: Optional[Dict[str, Any]] = None,
        scheduler_kwargs: Optional[Dict[str, Any]] = None,
        target_nan_mask: Union[int, float, str, type(None)] = None,
        metrics: Dict[str, Callable] = None,
        metrics_on_progress_bar: List[str] = [],
        tensorboard_save_dir: str = "logs",
    ):
        r"""
        A class that allows to use regression or classification models easily
        with Pytorch-Lightning.

        Parameters:
            model_class:
                pytorch module used to create a model

            model_kwargs:
                Key-word arguments used to initialize the model from `model_class`.

            loss_fun:
                Loss function used during training.
                Acceptable strings are 'mse', 'bce', 'mae', 'cosine'.
                Otherwise, a callable object must be provided, with a method `loss_fun._get_name()`.

            random_seed:
                The random seed used by Pytorch to initialize random tensors.

            optim_kwargs:
                Dictionnary used to initialize the optimizer, with possible keys below.

                - lr `float`: Learning rate (Default=`1e-3`)
                - weight_decay `float`: Weight decay used to regularize the optimizer (Default=`0.`)

            lr_reduce_on_plateau_kwargs:
                Dictionnary for the reduction of learning rate when reaching plateau, with possible keys below.

                - factor `float`: Factor by which to reduce the learning rate (Default=`0.5`)
                - patience `int`: Number of epochs without improvement to wait before reducing
                  the learning rate (Default=`10`)
                - mode `str`: One of min, max. In min mode, lr will be reduced when the quantity
                  monitored has stopped decreasing; in max mode it will be reduced when the quantity
                  monitored has stopped increasing. (Default=`"min"`).
                - min_lr `float`: A scalar or a list of scalars. A lower bound on the learning rate
                  of all param groups or each group respectively (Default=`1e-4`)

            scheduler_kwargs:
                Dictionnary for the scheduling of the learning rate modification

                - monitor `str`: metric to track (Default=`"loss/val"`)
                - interval `str`: Whether to look at iterations or epochs (Default=`"epoch"`)
                - strict `bool`: if set to True will enforce that value specified in monitor is available
                  while trying to call scheduler.step(), and stop training if not found. If False will
                  only give a warning and continue training (without calling the scheduler). (Default=`True`)
                - frequency `int`: **TODO: NOT REALLY SURE HOW IT WORKS!** (Default=`1`)

            target_nan_mask:
                TODO: It's not implemented for the metrics yet!!

                - None: Do not change behaviour if there are nans

                - int, float: Value used to replace nans. For example, if `target_nan_mask==0`, then
                  all nans will be replaced by zeros

                - 'ignore': Nans will be ignored when computing the loss.

            metrics:
                A dictionnary of metrics to compute on the prediction, other than the loss function.
                These metrics will be logged into TensorBoard.

            metrics_on_progress_bar:
                The metrics names from `metrics` to display also on the progress bar of the training

            tensorboard_save_dir:
                Directory where to save the tensorboard output files.

        """

        self.save_hyperparameters()

        torch.random.manual_seed(random_seed)
        np.random.seed(random_seed)

        super().__init__()
        self.model = model_class(**model_kwargs)

        # Basic attributes
        self.loss_fun = self.parse_loss_fun(loss_fun)
        self.random_seed = random_seed
        self.target_nan_mask = target_nan_mask
        self.metrics = metrics if metrics is not None else {}
        self.metrics_on_progress_bar = metrics_on_progress_bar
        self.n_params = sum(p.numel() for p in self.parameters() if p.requires_grad)
        self.lr_reduce_on_plateau_kwargs = lr_reduce_on_plateau_kwargs
        self.optim_kwargs = optim_kwargs
        self.scheduler_kwargs = scheduler_kwargs
        self.tensorboard_save_dir = tensorboard_save_dir

        # Set the default value for the optimizer
        self.optim_kwargs = optim_kwargs if optim_kwargs is not None else {}
        self.optim_kwargs.setdefault("lr", 1e-3)
        self.optim_kwargs.setdefault("weight_decay", 0.0)

        self.lr_reduce_on_plateau_kwargs = (
            lr_reduce_on_plateau_kwargs if lr_reduce_on_plateau_kwargs is not None else {}
        )
        self.lr_reduce_on_plateau_kwargs.setdefault("factor", 0.5)
        self.lr_reduce_on_plateau_kwargs.setdefault("patience", 10)
        self.lr_reduce_on_plateau_kwargs.setdefault("min_lr", 1e-4)

        self.optim_kwargs = optim_kwargs if optim_kwargs is not None else {}
        self.scheduler_kwargs.setdefault("monitor", "loss/val")
        self.scheduler_kwargs.setdefault("interval", "epoch")
        self.scheduler_kwargs.setdefault("frequency", 1)
        self.scheduler_kwargs.setdefault("strict", True)

        monitor = scheduler_kwargs["monitor"].split("/")[0]
        self.epoch_summary = EpochSummary(
            monitor, monitor_greater=False, metrics_on_progress_bar=self.metrics_on_progress_bar
        )

    @staticmethod
    def parse_loss_fun(loss_fun: Union[str, Callable]) -> Callable:
        r"""
        Parse the loss function from a string

        Parameters:
            loss_fun:
                A callable corresponding to the loss function or a string
                specifying the loss function from `LOSS_DICT`. Accepted strings are:
                "mse", "bce", "l1", "mae", "cosine".

        Returns:
            Callable:
                Function or callable to compute the loss, takes `preds` and `targets` as inputs.
        """

        if isinstance(loss_fun, str):
            loss_fun = LOSS_DICT[loss_fun]
        elif not callable(loss_fun):
            raise ValueError(f"`loss_fun` must be `str` or `callable`. Provided: {type(loss_fun)}")

        return loss_fun

    def forward(self, inputs: Dict):
        r"""
        Returns the result of `self.model.forward(*inputs)` on the inputs.
        """
        out = self.model.forward(inputs["features"])
        return out

    def configure_optimizers(self):
        optimiser = torch.optim.Adam(self.parameters(), **self.optim_kwargs)

        scheduler = {
            "scheduler": ReduceLROnPlateau(optimizer=optimiser, **self.lr_reduce_on_plateau_kwargs),
            **self.scheduler_kwargs,
        }
        return [optimiser], [scheduler]

    @staticmethod
    def compute_loss(
        preds: torch.Tensor,
        targets: torch.Tensor,
        loss_fun: Callable,
        target_nan_mask: Union[Type, str] = "ignore",
    ) -> torch.Tensor:
        r"""
        Compute the loss using the specified loss function, and dealing with
        the nans in the `targets`.

        Parameters:
            preds:
                Predicted values

            targets:
                Target values

            target_nan_mask:

                - None: Do not change behaviour if there are nans

                - int, float: Value used to replace nans. For example, if `target_nan_mask==0`, then
                  all nans will be replaced by zeros

                - 'ignore': Nans will be ignored when computing the loss.

            loss_fun:
                Loss function to use

        Returns:
            torch.Tensor:
                Resulting loss
        """
        if target_nan_mask is None:
            pass
        elif isinstance(target_nan_mask, (int, float)):
            targets[torch.isnan(targets)] = target_nan_mask
        elif target_nan_mask == "ignore":
            nans = torch.isnan(targets)
            targets = targets[~nans]
            preds = preds[~nans]
        else:
            raise ValueError(f"Invalid option `{target_nan_mask}`")

        loss = loss_fun(preds, targets)

        return loss

    def get_metrics_logs(
        self, preds: torch.Tensor, targets: torch.Tensor, step_name: str, loss_name: str
    ) -> Dict[str, Any]:
        r"""
        Get the logs for the loss and the different metrics, in a format compatible with
        Pytorch-Lightning.

        Parameters:
            preds:
                Predicted values

            targets:
                Target values

            step_name:
                A string to mention whether the metric is computed on the training,
                validation or test set.

                - "train": On the training set
                - "val": On the validation set
                - "test": On the test set

            loss_name:
                Name of the loss to display in tensorboard

        Returns:
            A dictionary with the keys value being:

            - `loss_name`: The value of the loss
            - `"log"`: A dictionary of type `Dict[str, torch.Tensor]`
                containing the metrics to log on tensorboard.
        """

        targets = targets.to(dtype=preds.dtype, device=preds.device)
        loss = self.compute_loss(
            preds=preds, targets=targets, target_nan_mask=self.target_nan_mask, loss_fun=self.loss_fun
        )

        # Compute the metrics always used in regression tasks
        metric_logs = {f"{self.loss_fun._get_name()}/{step_name}": loss}
        metric_logs[f"mean_pred/{step_name}"] = torch.mean(preds)
        metric_logs[f"std_pred/{step_name}"] = torch.std(preds)

        # Compute the additional metrics
        # TODO: NaN mask `target_nan_mask` not implemented here
        for key, metric in self.metrics.items():
            metric_name = f"{key}/{step_name}"
            try:
                metric_logs[metric_name] = metric(preds, targets)
            except:
                metric_logs[metric_name] = torch.tensor(float("nan"))

        return loss, metric_logs

    def _general_step(self, batch: Tuple[torch.Tensor], batch_idx: int) -> Dict[str, Any]:
        r"""Common code for training_step, validation_step and testing_step"""
        y = batch.pop("labels")
        preds = self.forward(batch)
        step_dict = {"preds": preds, "targets": y}
        return step_dict

    def training_step(self, batch: Tuple[torch.Tensor], batch_idx: int) -> Dict[str, Any]:
        step_dict = self._general_step(batch=batch, batch_idx=batch_idx)
        loss, metrics_logs = self.get_metrics_logs(
            preds=step_dict["preds"], targets=step_dict["targets"], step_name="train", loss_name="loss"
        )

        step_dict.update(metrics_logs)
        step_dict["loss"] = loss

        self.logger.log_metrics(metrics_logs, step=self.global_step)

        return step_dict

    def validation_step(self, batch: Tuple[torch.Tensor], batch_idx: int) -> Dict[str, Any]:
        return self._general_step(batch=batch, batch_idx=batch_idx)

    def testing_step(self, batch: Tuple[torch.Tensor], batch_idx: int) -> Dict[str, Any]:
        return self._general_step(batch=batch, batch_idx=batch_idx)

    def _general_epoch_end(self, outputs: Dict[str, Any], step_name: str) -> None:
        r"""Common code for training_epoch_end, validation_epoch_end and testing_epoch_end"""

        # Transform the list of dict of dict, into a dict of list of dict
        preds = torch.cat([out["preds"] for out in outputs], dim=0)
        targets = torch.cat([out["targets"] for out in outputs], dim=0)
        loss_name = f"loss/{step_name}"
        loss, metrics_logs = self.get_metrics_logs(
            preds=preds, targets=targets, step_name=step_name, loss_name=loss_name
        )

        self.epoch_summary.set_results(
            name=step_name,
            predictions=preds,
            targets=targets,
            loss=loss,
            metrics=metrics_logs,
            n_epochs=self.current_epoch,
        )

        return metrics_logs

    def training_epoch_end(self, outputs: Dict):

        self._general_epoch_end(outputs=outputs, step_name="train")

    def validation_epoch_end(self, outputs: List):

        metrics_logs = self._general_epoch_end(outputs=outputs, step_name="val")

        lr = self.optimizers().param_groups[0]["lr"]
        metrics_logs["lr"] = lr
        metrics_logs["n_epochs"] = self.current_epoch
        self.logger.log_metrics(metrics_logs, step=self.global_step)

        # Save yaml file with the metrics summaries
        full_dict = {}
        full_dict.update(self.epoch_summary.get_dict_summary())
        tb_path = self.logger.log_dir
        with open(f"{tb_path}/metrics.yaml", "w") as file:
            yaml.dump(full_dict, file)

        return metrics_logs

    def testing_epoch_end(self, outputs: List):

        metrics_logs = self._general_epoch_end(outputs=outputs, step_name="test")

        # Save yaml file with the metrics summaries
        full_dict = {}
        full_dict.update(self.epoch_summary.get_dict_summary())
        tb_path = self.logger.log_dir
        with open(f"{tb_path}/metrics.yaml", "w") as file:
            yaml.dump(full_dict, file)

    def on_train_start(self):
        self.logger.log_hyperparams(self.hparams, self.epoch_summary.get_results("val").metrics)

    def get_progress_bar_dict(self) -> Dict[str, float]:
        prog_dict = super().get_progress_bar_dict()
        results_on_progress_bar = self.epoch_summary.get_results_on_progress_bar("val")
        prog_dict["loss/val"] = self.epoch_summary.summaries["val"].loss.tolist()
        prog_dict.update(results_on_progress_bar)
        return prog_dict

    def summarize(self, mode: str = ModelSummaryExtended.MODE_DEFAULT, to_print=True) -> ModelSummaryExtended:
        r"""
        Provide a summary of the class, usually to be printed
        """
        model_summary = None

        if isinstance(mode, int):
            mode = ModelSummaryExtended.MODES[mode - 1]

        if mode in ModelSummaryExtended.MODES:
            model_summary = ModelSummaryExtended(self, mode=mode)
            if to_print:
                log.info("\n" + str(model_summary))
        elif mode is not None:
            raise MisconfigurationException(
                f"`mode` can be None, {', '.join(ModelSummaryExtended.MODES)}, got {mode}"
            )

        return model_summary

    def __repr__(self) -> str:
        r"""
        Controls how the class is printed
        """
        model_str = self.model.__repr__()
        summary_str = self.summarize(to_print=False).__repr__()

        return model_str + "\n\n" + summary_str
