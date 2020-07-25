# coding=utf-8

import torch
from torch import nn
import torch.nn.functional as F
import numpy as np
import math

def apply_mask(align_score, mask, prev_idxs):
    """ apply mask for shutdown previous indexs that already chose
    Args:
    align_score : scores
    mask : mask for content indexs with booleans
    prev_idxs: Previous indexs that already the algorithm chose
    Return:
    align_score
    """
    if mask is None:
        mask = torch.zeros(align_score.size()).byte() # Byte Tensor
        if torch.cuda.is_available():
            mask = mask.cuda()
    
    mask_ = mask.clone()
    if prev_idxs is not None:
        mask_[[x for x in range(align_score.size(0))],:, prev_idxs] = 1
        align_score[mask_] = -np.inf
    return align_score, mask_
class Attention(nn.Module):
    """ Attention layer
    Args:
      attn_type : attention type ["dot", "general"]
      dim : hidden dimension size
    """
    def __init__(self, attn_type, dim, bz_size, C=None):
        super().__init__()
        self.attn_type = attn_type
        self.C = C
        self.tanh = nn.Tanh()
        bias_out = attn_type == "mlp"
        self.linear_out = nn.Linear(dim *2, dim, bias_out)
        self.conv_proj = nn.Conv1d(dim, dim, 1 , 1)
        self.v = 0
        if self.attn_type == "RL":
            self.W_ref = nn.Linear(dim, dim, bias=False)
            self.W_q = nn.Linear(dim, dim, bias=False)
            v = torch.FloatTensor(dim)
            if torch.cuda.is_available():
                v = v.cuda()
                self.W_ref = self.W_ref.cuda()
                self.W_q = self.W_q.cuda()
                self.conv_proj = self.conv_proj.cuda()
            self.v = nn.Parameter(v)
            self.v.data.uniform_(-(1. / math.sqrt(dim)) , 1. / math.sqrt(dim))
        elif self.attn_type == "general":
            self.linear = nn.Linear(dim, dim, bias=False)
        elif self.attn_type == "dot":
            pass
        else:
            raise NotImplementedError()
  
    def score(self, src, tgt):
        """ Attention score calculation
        Args:
        src : source values (bz, src_len, dim)
        tgt : target values (bz, tgt_len, dim)
          """
        # bz, src_len, dim = src.size()
        # _, tgt_len, _ = tgt.size()
        
        if self.attn_type in ["general", "dot", "RL"]:
            tgt_ = tgt
            src_ = src
            if self.attn_type == "RL":
                tgt_ = self.W_q(tgt_)
                src_ = self.W_ref(src)
                
                tgt_ = tgt_.repeat(1, src_.size(1), 1)
            elif self.attn_type == "general":
                tgt_ = self.linear(tgt_)
            # src_ = src_.transpose(1, 2)
            
            if self.attn_type in ["general", "dot"]:
                return torch.bmm(tgt_, src_)
            elif self.attn_type=="RL":#type(self.v)=='torch.Tensor':
                
                v = self.v.unsqueeze(0).expand(tgt_.size(0), len(self.v)).unsqueeze(1)
                
                u = torch.bmm(v,self.tanh(tgt_ + src_).transpose(1, 2))
                if self.C:
                    return self.C*self.tanh(u)
                else:
                    return u
  
    def forward(self, src, tgt, mask, prev_idxs, attention_type="Attention"):
        """
        Args:
        src : source values (bz, src_len, dim). enc_i or ref in Bello's Paper
        tgt : target values (bz, tgt_len, dim). dec_i or q
        src_lengths : source values length
        """
        if tgt.dim() == 2:
            one_step = True
            src = src.unsqueeze(1)
        else:
            one_step = False
      
        align_score = self.score(src, tgt)
      
        if attention_type=="Attention":
            align_score, mask = apply_mask(align_score, mask, prev_idxs)
        # Normalize weights
        logits = F.softmax(align_score.squeeze(), -1)
        
        if len(logits.size())!=1:
            logits = logits.unsqueeze(2).transpose(1,2)
          
        attn_h = 0
        if self.attn_type in ["general", "dot"]:
            c = torch.bmm(logits, src)
            concat_c = torch.cat([c, tgt], -1)
            attn_h = self.linear_out(concat_c)
        if one_step:
            attn_h = attn_h.squeeze(1)
            logits = logits.squeeze(1)
        else:
            src = src.transpose(1, 2)
            attn_h = self.conv_proj(src)
        return attn_h, logits, mask # [batch_size, hidden_dim, embedding_dim], [batch_size, 1, embedding_dim]