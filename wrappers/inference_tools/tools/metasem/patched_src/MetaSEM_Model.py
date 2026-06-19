import torch
import torch.nn as nn
from torch.autograd import Variable
from torch.nn.parameter import Parameter


class Inference(nn.Module):
    def __init__(self, input, hidden, output, K, alpha):
        super(Inference, self).__init__()
        self.output = output
        self.K = K
        self.alpha = alpha
        self.n_class = output
        self.Linear1 = nn.Linear(input, hidden, bias=True)
        self.tanh = nn.Tanh()
        self.inference_get_logits = nn.Sequential(
            nn.Tanh(),
            nn.Dropout(0.2),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
            nn.Linear(hidden, output),
        )

    def soft_cross_entropy(self, y_hat, y_soft, weight=None):
        if weight is None:
            return -torch.sum(torch.mul(torch.log_softmax(y_hat, dim=-1), y_soft)) / self.n_class
        return -torch.sum(
            torch.mul(weight, torch.mul(torch.log_softmax(y_hat, dim=-1), y_soft))
        ) / self.n_class

    def get_logits(self, x):
        for layer in self.inference_get_logits:
            x = layer(x)
        return x

    def forward(self, x):
        x = self.tanh(self.Linear1(x))
        return self.inference_get_logits(x)


class MetaGRNInference(nn.Module):
    def __init__(self, K, adj, y0, opt):
        super(MetaGRNInference, self).__init__()
        self.opt = opt
        self.K = K
        self.y0 = y0
        num_genes = int(adj.shape[0])
        hidden = int(getattr(opt, "n_hidden", 64))
        self.adj = nn.Parameter(Variable(adj.float(), requires_grad=True, name="adj_A"))
        self.weight = nn.Linear(num_genes, hidden, bias=True)
        self.weight2 = nn.Linear(hidden, num_genes, bias=False)
        self.ys = []
        y = self.y0
        for _ in range(self.K):
            y = torch.matmul(self.adj, y)
            self.ys.append(y)
        self.ys = torch.stack(self.ys).transpose(0, 1)

    def forward(self, x):
        q = self.weight(x)
        q2 = torch.relu(q)
        alpha = torch.sigmoid(self.weight2(q2))
        return (torch.matmul(alpha, self.adj)) + self.opt.alpha * alpha
