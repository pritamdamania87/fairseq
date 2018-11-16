# Copyright (c) 2017-present, Facebook, Inc.
# All rights reserved.
#
# This source code is licensed under the license found in the LICENSE file in
# the root directory of this source tree. An additional grant of patent rights
# can be found in the PATENTS file in the same directory.

import math

import numpy as np
import torch

from . import FairseqDataset


class TokenBlockDataset(FairseqDataset):
    """Break a 1d tensor of tokens into blocks.

    The blocks are fetched from the original tensor so no additional memory is allocated.

    Args:
        tokens: 1d tensor of tokens to break into blocks
        sizes: sentence lengths (required for 'complete' and 'eos')
        block_size: maximum block size (ignored in 'eos' break mode)
        break_mode: Mode used for breaking tokens. Values can be one of:
            - 'none': break tokens into equally sized blocks (up to block_size)
            - 'complete': break tokens into blocks (up to block_size) such that
                blocks contains complete sentences, although block_size may be
                exceeded if some sentences exceed block_size
            - 'eos': each block contains one sentence (block_size is ignored)
        include_targets: return next tokens as targets
    """

    def __init__(self, ds, block_size, pad, eos, break_mode=None, include_targets=False):
        super().__init__()
        self.dataset = ds
        self.pad = pad
        self.eos = eos
        self.include_targets = include_targets
        self.slice_indices = []
        self.cache_index = {}
        sizes = ds.sizes

        if break_mode is None or break_mode == 'none':
            total_size = sum(sizes)
            length = math.ceil(total_size / block_size)

            def block_at(i):
                start = i * block_size
                end = min(start + block_size, total_size)
                return (start, end)

            self.slice_indices = [block_at(i) for i in range(length)]
        elif break_mode == 'complete':
            tok_idx = 0
            sz_idx = 0
            curr_size = 0
            while sz_idx < len(sizes):
                if curr_size + sizes[sz_idx] <= block_size or curr_size == 0:
                    curr_size += sizes[sz_idx]
                    sz_idx += 1
                else:
                    self.slice_indices.append((tok_idx, tok_idx + curr_size))
                    tok_idx += curr_size
                    curr_size = 0
            if curr_size > 0:
                self.slice_indices.append((tok_idx, tok_idx + curr_size))
        elif break_mode == 'eos':
            curr = 0
            for sz in sizes:
                # skip samples with just 1 example (which would be just the eos token)
                if sz > 0:
                    self.slice_indices.append((curr, curr + sz))
                curr += sz
        else:
            raise ValueError('Invalid break_mode: ' + break_mode)

        self.sizes = np.array([e - s for s, e in self.slice_indices])

    def __getitem__(self, index):
        s, e = self.cache_index[index]

        item = torch.from_numpy(self.cache[s:e]).long()

        if self.include_targets:
            # target is the sentence, for source, rotate item one token to the left (would start with eos)
            # past target is rotated to the left by 2 (padded if its first)
            if s == 0:
                source = np.concatenate([[self.eos], self.cache[0:e - 1]])
                past_target = np.concatenate([[self.pad, self.eos], self.cache[0:e - 2]])
            else:
                source = self.cache[s - 1: e - 1]
                if s == 1:
                    past_target = np.concatenate([[self.eos], self.cache[0:e - 2]])
                else:
                    past_target = self.cache[s - 2:e - 2]

            return torch.from_numpy(source).long(), item, torch.from_numpy(past_target).long()
        return item

    def __len__(self):
        return len(self.slice_indices)

    def prefetch(self, indices):
        indices.sort()
        total_size = 0
        for idx in indices:
            s, e = self.slice_indices[idx]
            total_size += e - s
        self.cache = np.empty(total_size, dtype=np.int32)
        start = 0
        for idx in indices:
            s, e = self.slice_indices[idx]
            self.dataset.read_into(s, self.cache[start:start + e - s])
            self.cache_index[idx] = (start, start + e - s)
            start += e - s

    @property
    def supports_prefetch(self):
        return True
