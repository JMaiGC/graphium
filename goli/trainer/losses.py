from typing import Iterable, Optional, Union

import torch
from torch import Tensor
from torch.nn import functional as F
from torch.nn.modules.loss import _WeightedLoss


class HybridCELoss(_WeightedLoss):
    def __init__(
        self,
        brackets: Union[Iterable[int], Tensor] = (0, 1, 2, 3, 4),
        regression_loss: str = "mse",
        alpha: float = 0.5,
        weight: Optional[Tensor] = None,
        reduction: str = "mean",
    ) -> None:
        """
        A hybrid between the regression loss (either MAE or MSE) and the cross entropy loss. Intended
        to be used with noisy regression datasets, for which the targets are assigned to binary brackets,
        and the task in transformed into a multi-class classification.

        Parameters:
            brackets: an iterable of integers assigned to each class. Expected to have the same size
                as the number of classes in the transformed regression task.
            regression_loss: type of regression loss, either 'mse' or 'mae'.
            alpha: weight assigned to the CE loss component. Must be a value in [0, 1] range.
            weight: a manual rescaling weight given to each class in the CE loss component.
                If given, has to be a Tensor of the same size as the number of classes.
            reduction: specifies the reduction to apply to the output: 'none' | 'mean' | 'sum'.
                'none': no reduction will be applied, 'mean': the sum of the output will be divided
                by the number of elements in the output, 'sum': the output will be summed.
        """
        super().__init__(weight=weight, reduction=reduction)

        if regression_loss not in ["mae", "mse"]:
            raise ValueError(
                f"Expected regression_loss to be in {{'mae', 'mse'}}, received {regression_loss}."
            )

        if alpha < 0 or alpha > 1:
            raise ValueError(
                f"Expected alpha to be in the [0, 1] range, received {alpha}."
            )

        if not isinstance(brackets, Tensor):
            brackets = Tensor(brackets)

        self.brackets = brackets
        self.regression_loss = F.l1_loss if regression_loss == "mae" else F.mse_loss
        self.alpha = alpha

    def forward(self, input: Tensor, target: Tensor) -> Tensor:
        """
        Parameters:
            input: (batch_size x n_classes) tensor of probabilities predicted for each bracket.
            target: (batch_Size x n_classes) tensor of one-hot encoded target brackets.
        """
        regression_input = torch.inner(input, self.brackets)
        regression_target = target.argmax(-1)
        regression_loss = self.regression_loss(
            regression_input, regression_target, reduction=self.reduction
        )

        ce_loss = F.cross_entropy(
            input, target, weight=self.weight, reduction=self.reduction
        )

        return self.alpha * ce_loss + (1 - self.alpha) * regression_loss
