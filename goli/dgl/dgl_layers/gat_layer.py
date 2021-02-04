import torch
import torch.nn as nn
import torch.nn.functional as F

from dgl.nn.pytorch import GATConv
from dgl import DGLGraph

from goli.dgl.dgl_layers.base_dgl_layer import BaseDGLLayer

"""
    GAT: Graph Attention Network
    Graph Attention Networks (Veličković et al., ICLR 2018)
    https://arxiv.org/abs/1710.10903
"""


class GATLayer(BaseDGLLayer):
    """
    GAT: Graph Attention Network
    Graph Attention Networks (Veličković et al., ICLR 2018)
    https://arxiv.org/abs/1710.10903

    The implementation is built on top of the DGL ``GATCONV`` layer

        Parameters
        ------------

        in_dim: int
            Input feature dimensions of the layer

        out_dim: int
            Output feature dimensions of the layer

        num_heads: int
            Number of heads in Multi-Head Attention

        activation: str, Callable, Default="relu"
            activation function to use in the layer

        dropout: float, Default=0.
            The ratio of units to dropout. Must be between 0 and 1

        batch_norm: bool, Default=False
            Whether to use batch normalization
    """

    def __init__(
        self,
        in_dim: int,
        out_dim: int,
        num_heads,
        activation="elu",
        dropout: float = 0.0,
        batch_norm: bool = False,
    ):

        super().__init__(
            in_dim=in_dim,
            out_dim=out_dim,
            activation=activation,
            dropout=dropout,
            batch_norm=batch_norm,
        )

        self.num_heads = num_heads

        self.gatconv = GATConv(
            in_feats=self.in_dim,
            out_feats=self.out_dim,
            num_heads=self.num_heads,
            feat_drop=self.dropout,
            attn_drop=self.dropout,
            activation=None,  # Activation is applied after
        )

    def forward(self, g: DGLGraph, h: torch.Tensor) -> torch.Tensor:
        r"""
        Apply the graph convolutional layer, with the specified activations,
        normalizations and dropout.

        Parameters
        ------------

        g: dgl.DGLGraph
            graph on which the convolution is done

        h: torch.Tensor(..., N, Din)
            Node feature tensor, before convolution.
            N is the number of nodes, Din is the input dimension

        Returns
        ---------

        h: torch.Tensor(..., N, Dout)
            Node feature tensor, after convolution.
            N is the number of nodes, Dout is the output dimension

        """

        h = self.gatconv(g, h).flatten(1)
        self.apply_norm_activation_dropout(h, batch_norm=True, activation=True, dropout=False)

        return h

    @staticmethod
    def layer_supports_edges() -> bool:
        r"""
        Return a boolean specifying if the layer type supports edges or not.

        Returns
        ---------

        supports_edges: bool
            Always ``False`` for the current class
        """
        return False

    def layer_inputs_edges(self) -> bool:
        r"""
        Return a boolean specifying if the layer type
        uses edges as input or not.
        It is different from ``layer_supports_edges`` since a layer that
        supports edges can decide to not use them.

        Returns
        ---------

        uses_edges: bool
            Always ``False`` for the current class
        """
        return False

    def layer_outputs_edges(self) -> bool:
        r"""
        Abstract method. Return a boolean specifying if the layer type
        uses edges as input or not.
        It is different from ``layer_supports_edges`` since a layer that
        supports edges can decide to not use them.

        Returns
        ---------

        uses_edges: bool
            Always ``False`` for the current class
        """
        return False

    def get_out_dim_factor(self) -> int:
        r"""
        Get the factor by which the output dimension is multiplied for
        the next layer.

        For standard layers, this will return ``1``.

        But for others, such as ``GatLayer``, the output is the concatenation
        of the outputs from each head, so the out_dim gets multiplied by
        the number of heads, and this function should return the number
        of heads.

        Returns
        ---------

        dim_factor: int
            Always ``self.num_heads`` for the current class
        """
        return self.num_heads
