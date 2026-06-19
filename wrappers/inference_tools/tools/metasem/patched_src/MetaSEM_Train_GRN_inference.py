from SRC.MetaSEM_tool import *
from SRC.MetaSEM_Model import *

import copy
import math
import os
import random

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader
from torch.utils.data.dataset import TensorDataset


def _device():
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _standardize_expression(data_values):
    values = np.asarray(data_values, dtype=float)
    means = []
    stds = []
    for idx in range(values.shape[1]):
        tmp = values[:, idx]
        nonzero = tmp[tmp != 0]
        if nonzero.size == 0:
            means.append(0.0)
            stds.append(1.0)
            continue
        means.append(float(nonzero.mean()))
        std = float(nonzero.std())
        stds.append(std if math.isfinite(std) and std > 0 else 1.0)
    means = np.asarray(means, dtype=float)
    stds = np.asarray(stds, dtype=float)
    values = (values - means) / stds
    values[np.isnan(values)] = 0
    values[np.isinf(values)] = 0
    values = np.maximum(values, -10)
    values = np.minimum(values, 10)
    med = np.median(values)
    row_sums = np.sum(values, axis=1)
    row_sums[row_sums == 0] = 1.0
    return np.exp(med * (values / row_sums[:, None]))


def extractEdgesFromMatrix(m, geneNames, TFmask):
    geneNames = np.array(geneNames)
    mat = copy.deepcopy(m)
    num_nodes = mat.shape[0]
    mat_indicator_all = np.zeros([num_nodes, num_nodes])
    if TFmask is not None:
        mat = mat * TFmask
    mat_indicator_all[abs(mat) > 0] = 1
    idx_rec, idx_send = np.where(mat_indicator_all)
    edges_df = pd.DataFrame(
        {
            "TF": geneNames[idx_send],
            "Target": geneNames[idx_rec],
            "EdgeWeight": mat[idx_rec, idx_send],
        }
    )
    return edges_df.sort_values("EdgeWeight", ascending=False)


def evaluate(A, truth_edges, Evaluate_Mask):
    num_nodes = A.shape[0]
    num_truth_edges = len(truth_edges)
    A = abs(A)
    if Evaluate_Mask is None:
        Evaluate_Mask = np.ones_like(A) - np.eye(len(A))
    A = A * Evaluate_Mask
    A_val = list(np.sort(abs(A.reshape(-1, 1)), 0)[:, 0])
    A_val.reverse()
    if num_truth_edges >= len(A_val):
        return 0, 0.0
    cutoff_all = A_val[num_truth_edges]
    A_indicator_all = np.zeros([num_nodes, num_nodes])
    A_indicator_all[abs(A) > cutoff_all] = 1
    idx_rec, idx_send = np.where(A_indicator_all)
    A_edges = set(zip(idx_send, idx_rec))
    overlap_A = A_edges.intersection(truth_edges)
    denom = (num_truth_edges ** 2) / max(np.sum(Evaluate_Mask), 1)
    if denom == 0:
        return len(overlap_A), 0.0
    return len(overlap_A), 1.0 * len(overlap_A) / denom


class Train_inference:
    def __init__(self, opt):
        self.opt = opt
        os.makedirs(opt.save_name, exist_ok=True)
        self.device = _device()

    def initalize_A(self, data):
        num_genes = data.shape[1]
        A = np.ones([num_genes, num_genes]) / max(num_genes - 1, 1)
        A = A + np.random.randn(num_genes * num_genes).reshape([num_genes, num_genes]) * 0.0005
        for idx in range(len(A)):
            A[idx, idx] = 0
        return A

    def _one_minus_A_t(self, adj):
        eye = torch.eye(adj.shape[0], device=self.device, dtype=adj.dtype)
        return eye - adj.transpose(0, 1)

    def data_prepare(self, input_path, net_path):
        ground_truth = pd.read_csv(net_path, header=0)
        data = pd.read_csv(input_path, header=0, index_col=0)
        gene_name = [str(value) for value in data.columns]
        data_values = data.to_numpy(dtype=float)
        data_values = _standardize_expression(data_values)
        data = pd.DataFrame(data_values, index=[str(x) for x in data.index], columns=gene_name)
        TF = set(ground_truth["Gene1"].astype(str))
        All_gene = set(ground_truth["Gene1"].astype(str)) | set(ground_truth["Gene2"].astype(str))
        num_genes, num_nodes = data.shape[1], data.shape[0]
        self.opt.net_size = num_genes
        Evaluate_Mask = np.zeros([num_genes, num_genes])
        TF_mask = np.zeros([num_genes, num_genes])
        for i, target_gene in enumerate(data.columns):
            for j, regulator_gene in enumerate(data.columns):
                if i == j:
                    continue
                if regulator_gene in TF and target_gene in All_gene:
                    Evaluate_Mask[i, j] = 1
                if regulator_gene in TF:
                    TF_mask[i, j] = 1
        feat_train = torch.FloatTensor(data.values)
        if self.opt.is_label:
            truth_df = pd.DataFrame(np.zeros([num_genes, num_genes]), index=data.columns, columns=data.columns)
            for i in range(ground_truth.shape[0]):
                target = str(ground_truth.iloc[i, 1])
                regulator = str(ground_truth.iloc[i, 0])
                if target in truth_df.index and regulator in truth_df.columns:
                    truth_df.loc[target, regulator] = 1
        else:
            truth_df = pd.DataFrame(np.ones([num_genes, num_genes]), index=data.columns, columns=data.columns)
        A_truth = truth_df.values
        idx_rec, idx_send = np.where(A_truth)
        truth_edges = set(zip(idx_send, idx_rec))
        y0 = torch.ones(size=[num_genes])
        for idx in truth_edges:
            if random.randint(1, 100) > 99:
                y0[idx[0]] = y0[idx[0]] + 1
                y0[idx[1]] = y0[idx[1]] + 1
        truth_matrix = y0.reshape([1, num_genes]).repeat(num_nodes, 1)
        pseudo_data = torch.FloatTensor(data.values)
        return feat_train, truth_matrix, pseudo_data, Evaluate_Mask, num_nodes, num_genes, data, truth_edges, TF_mask, gene_name

    def train_model(self, input_path, net_path):
        (
            feat_train,
            truth_matrix,
            pseudo_data,
            Evaluate_Mask,
            _num_nodes,
            num_genes,
            data,
            truth_edges,
            TFmask2,
            gene_name,
        ) = self.data_prepare(input_path, net_path)
        epochs = int(getattr(self.opt, "epochs", getattr(self.opt, "epoch", 20)))
        batch_size = int(getattr(self.opt, "batch_size", 64))
        hidden = int(getattr(self.opt, "hidden_size", 64))
        for epoch in range(epochs):
            train_data = TensorDataset(feat_train, truth_matrix, pseudo_data)
            dataloader = DataLoader(train_data, batch_size=batch_size, shuffle=True, drop_last=False)
            adj_A_init = self.initalize_A(data)
            adj_A_t = self._one_minus_A_t(torch.tensor(adj_A_init, dtype=torch.float32, device=self.device))
            y0 = torch.zeros(size=adj_A_t.shape, device=self.device)
            if self.opt.is_label:
                for idx in truth_edges:
                    y0[idx] = 1
            main_net = Inference(num_genes, hidden, num_genes, 3, 3).to(self.device)
            meta_net = MetaGRNInference(3, adj_A_t.detach().cpu(), y0.detach().cpu(), opt=self.opt).to(self.device)
            main_net_optimizer = torch.optim.Adam(main_net.parameters(), lr=self.opt.lr)
            meta_net_optimizer = torch.optim.Adam(meta_net.parameters(), lr=self.opt.lr_meta, weight_decay=0.00001)
            main_net_scheduler = torch.optim.lr_scheduler.StepLR(
                main_net_optimizer, step_size=self.opt.lr_step_size, gamma=self.opt.gamma
            )
            meta_net_scheduler = torch.optim.lr_scheduler.StepLR(
                meta_net_optimizer, step_size=self.opt.lr_step_size_meta, gamma=self.opt.gamma_meta
            )
            meta_net.train()
            main_net.train()

            loss = None
            Ep, Epr = 0, 0.0
            pseudo_batches = []
            for batch in dataloader:
                inputs_ori, label_true, pseudo_ori = batch
                inputs = inputs_ori.to(self.device)
                pseudo = pseudo_ori.to(self.device)
                label_true = label_true.to(self.device)
                meta_net_optimizer.zero_grad()
                meta_loss_unrolled_backward(
                    main_net,
                    main_net_optimizer,
                    meta_net,
                    pseudo,
                    inputs,
                    inputs,
                    label_true,
                    lr_main=0.01,
                )
                meta_net_optimizer.step()
                y_pseudo = meta_net(inputs).detach()
                main_net_optimizer.zero_grad()
                output = main_net(inputs)
                pseudo_temp_current = (output.detach().cpu() * pseudo_ori) * 0.5 + pseudo_ori
                pseudo_batches.append(pseudo_temp_current)
                loss = (
                    0.005 * main_net.soft_cross_entropy(output, y_pseudo)
                    + 0.005 * torch.sum(main_net.Linear1.weight ** 2) / 2
                )
                loss.backward()
                main_net_optimizer.step()
                if self.opt.is_label:
                    Ep, Epr = evaluate(meta_net.adj.detach().cpu().numpy(), truth_edges, Evaluate_Mask)
            pseudo_data = torch.cat(pseudo_batches, dim=0) if pseudo_batches else pseudo_data
            raw_edges = extractEdgesFromMatrix(meta_net.adj.detach().cpu().numpy(), gene_name, TFmask2)
            raw_edges.to_csv(self.opt.tsv_path, sep="\t", index=False)
            loss_value = float(loss.item()) if loss is not None else 0.0
            print("epoch", epoch, "loss", loss_value, "Ep:", Ep, "Epr:", Epr)
            callback = getattr(self.opt, "progress_callback", None)
            if callback is not None:
                callback(epoch + 1, epochs)
            meta_net_scheduler.step()
            main_net_scheduler.step()
