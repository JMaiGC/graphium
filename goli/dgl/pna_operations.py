import torch
import numpy as np

EPS = 1e-5


def aggregate_mean(h, **kwargs):
    return torch.mean(h, dim=1)


def aggregate_max(h, **kwargs):
    return torch.max(h, dim=1)[0]


def aggregate_min(h, **kwargs):
    return torch.min(h, dim=1)[0]


def aggregate_std(h, **kwargs):
    return torch.sqrt(aggregate_var(h) + EPS)


def aggregate_var(h, **kwargs):
    h_mean_squares = torch.mean(h * h, dim=-2)
    h_mean = torch.mean(h, dim=-2)
    var = torch.relu(h_mean_squares - h_mean * h_mean)
    return var


def aggregate_moment(h, n=3, **kwargs):
    # for each node (E[(X-E[X])^n])^{1/n}
    # EPS is added to the absolute value of expectation before taking the nth root for stability
    h_mean = torch.mean(h, dim=1, keepdim=True)
    h_n = torch.mean(torch.pow(h - h_mean, n))
    rooted_h_n = torch.sign(h_n) * torch.pow(torch.abs(h_n) + EPS, 1.0 / n)
    return rooted_h_n


def aggregate_moment_3(h, **kwargs):
    return aggregate_moment(h, n=3)


def aggregate_moment_4(h, **kwargs):
    return aggregate_moment(h, n=4)


def aggregate_moment_5(h, **kwargs):
    return aggregate_moment(h, n=5)


def aggregate_sum(h, **kwargs):
    return torch.sum(h, dim=1)


# each scaler is a function that takes as input X (B x N x Din), adj (B x N x N) and
# avg_d (dictionary containing averages over training set) and returns X_scaled (B x N x Din) as output


def scale_identity(h, D=None, avg_d=None):
    return h


def scale_amplification(h, D, avg_d):
    # log(D + 1) / d * h     where d is the average of the ``log(D + 1)`` in the training set
    return h * (np.log(D + 1) / avg_d["log"])


def scale_attenuation(h, D, avg_d):
    # (log(D + 1))^-1 / d * X     where d is the average of the ``log(D + 1))^-1`` in the training set
    return h * (avg_d["log"] / np.log(D + 1))


PNA_AGGREGATORS = {
    "mean": aggregate_mean,
    "sum": aggregate_sum,
    "max": aggregate_max,
    "min": aggregate_min,
    "std": aggregate_std,
    "var": aggregate_var,
    "moment3": aggregate_moment_3,
    "moment4": aggregate_moment_4,
    "moment5": aggregate_moment_5,
}


PNA_SCALERS = {
    "identity": scale_identity,
    "amplification": scale_amplification,
    "attenuation": scale_attenuation,
}
