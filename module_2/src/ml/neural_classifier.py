import torch
import torch.nn as nn

torch.manual_seed(42)


class CharCNNClassifier(nn.Module):
    def __init__(self, vocab_size=520, embed_dim=128, num_filters=64,
                 kernel_sizes=None, num_classes=5, dropout=0.3):
        super().__init__()
        if kernel_sizes is None:
            kernel_sizes = [2, 3, 4]

        self.embedding = nn.Embedding(vocab_size, embed_dim, padding_idx=0)

        self.convs = nn.ModuleList([
            nn.Conv1d(in_channels=embed_dim, out_channels=num_filters,
                      kernel_size=k)
            for k in kernel_sizes
        ])

        concat_dim = num_filters * len(kernel_sizes)  # 64 * 3 = 192

        self.classifier = nn.Sequential(
            nn.Linear(concat_dim, 128),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(128, 64),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(64, num_classes),
        )

    def forward(self, x):
        # x: (batch, seq_len)
        embedded = self.embedding(x)          # (batch, seq_len, embed_dim)
        embedded = embedded.permute(0, 2, 1)  # (batch, embed_dim, seq_len)

        pooled = []
        for conv in self.convs:
            out = conv(embedded)              # (batch, num_filters, seq_len - k + 1)
            out = torch.relu(out)
            out = out.max(dim=2).values       # (batch, num_filters) — global max pooling
            pooled.append(out)

        concat = torch.cat(pooled, dim=1)     # (batch, 192)
        logits = self.classifier(concat)      # (batch, num_classes)
        return logits
