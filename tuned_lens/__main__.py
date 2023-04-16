"""Script to train or evaluate a set of tuned lenses for a language model."""

from typing import List, Optional, TypedDict, get_type_hints
from .scripts.lens import main as lens_main
from argparse import ArgumentParser
from contextlib import nullcontext, redirect_stdout
import os
import torch.distributed as dist
from .scripts.train_loop import cli_args as train_cli_args
from .scripts.downstream import cli_args as downstream_cli_args
from .scripts.eval_loop import cli_args as eval_cli_args


class SharedCliArgs:
    """Type hinting for CLI args used for both training and evaluation"""
    model_name: str
    dataset: Optional[List[str]]
    cpu_offload: bool
    fsdp: bool
    loss: str
    no_cache: bool
    per_gpu_batch_size: int
    random_model: bool
    residual_stats: bool
    revision: str
    seed: int
    slow_tokenizer: bool
    split: str
    sweep: str
    task: List[str]
    text_column: str
    tokenizer: str
    tokenizer_type: str
    token_shift: int


class Arg(TypedDict):
    """An argparse argument"""
    name_or_flags: List[str]
    options: dict


arg_types = get_type_hints(SharedCliArgs)

# Arguments shared by train and eval; see https://stackoverflow.com/a/56595689.
shared_cli_args: List[Arg] = [
    {
        "name_or_flags": ["model_name"],
        "options": {
            "type": arg_types["model_name"],
            "help": "Name of model to use in the Huggingface Hub.",
        },
    },
    {
        "name_or_flags": ["dataset"],
        "options": {
            "type": arg_types["dataset"],
            "default": ("the_pile", "all"),
            "nargs": "*",
            "help": "Name of dataset to use. Can either be a local .jsonl file or a "
                    "name suitable to be passed to the HuggingFace load_dataset "
                    "function.",
        },
    },
    {
        "name_or_flags": ["--cpu-offload"],
        "options": {
            "type": arg_types["cpu_offload"],
            "action": "store_true",
            "help": "Use CPU offloading. Must be combined with --fsdp.",
        },
    },
    {
        "name_or_flags": ["--fsdp"],
        "options": {
            "type": arg_types["fsdp"],
            "action": "store_true",
            "help": "Run the model with Fully Sharded Data Parallelism.",
        },
    },
    {
        "name_or_flags": ["--loss"],
        "options": {
            "type": arg_types["loss"],
            "default": "kl",
            "choices": ("ce", "kl"),
            "help": "Loss function to use.",
        },
    },
    {
        "name_or_flags": ["--no-cache"],
        "options": {
            "type": arg_types["no_cache"],
            "action": "store_true",
            "help": "Don't permanently cache the model on disk.",
        },
    },
    {
        "name_or_flags": ["--per-gpu-batch-size"],
        "options": {
            "type": arg_types["per_gpu_batch_size"],
            "default": 1,
            "help": "Number of samples to try to fit on a GPU at once.",
        },
    },
    {
        "name_or_flags": ["--random-model"],
        "options": {
            "type": arg_types["random_model"],
            "action": "store_true",
            "help": "Use a randomly initialized model instead of pretrained weights.",
        },
    },
    {
        "name_or_flags": ["--residual-stats"],
        "options": {
            "type": arg_types["residual_stats"],
            "action": "store_true",
            "help": "Save means and covariance matrices for states in the residual "
                    "stream.",
        },
    },
    {
        "name_or_flags": ["--revision"],
        "options": {
            "type": arg_types["revision"],
            "default": "main",
            "help": "Git revision to use for pretrained models.",
        },
    },
    {
        "name_or_flags": ["--seed"],
        "options": {
            "type": arg_types["seed"],
            "default": 42,
            "help": "Random seed for data shuffling.",
        },
    },
    {
        "name_or_flags": ["--slow-tokenizer"],
        "options": {
            "type": arg_types["slow_tokenizer"],
            "action": "store_true",
            "help": "Use a slow tokenizer.",
        },
    },
    {
        "name_or_flags": ["--split"],
        "options": {
            "type": arg_types["split"],
            "default": "validation",
            "help": "Split of the dataset to use.",
        },
    },
    {
        "name_or_flags": ["--sweep"],
        "options": {
            "type": arg_types["sweep"],
            "help": "Range of checkpoints to sweep over",
        },
    },
    {
        "name_or_flags": ["--task"],
        "options": {
            "type": arg_types["task"],
            "nargs": "+",
            "help": "lm-eval task to run the model on.",
        },
    },
    {
        "name_or_flags": ["--text-column"],
        "options": {
            "type": arg_types["text_column"],
            "default": "text",
            "help": "Column of the dataset containing text to run the model on.",
        },
    },
    {
        "name_or_flags": ["--tokenizer"],
        "options": {
            "type": arg_types["tokenizer"],
            "help": "Name of pretrained tokenizer to use from the Huggingface Hub. "
                    "If None, will use AutoTokenizer.from_pretrained('<model name>').",
        },
    },
    {
        "name_or_flags": ["--tokenizer-type"],
        "options": {
            "type": arg_types["tokenizer_type"],
            "help": "Name of tokenizer class to use. If None, will use AutoTokenizer.",
        },
    },
    {
        "name_or_flags": ["--token-shift"],
        "options": {
            "type": arg_types["token_shift"],
            "default": None,
            "help": "How to shift the labels wrt the input tokens (1 = next token, 0 = "
                    "current token, -1 = previous token, etc.)",
        },
    },
]


def run():
    """Run the script."""
    parser = ArgumentParser(
        description="Train or evaluate a set of tuned lenses for a language model.",
    )
    parent_parser = ArgumentParser(add_help=False)
    for arg in shared_cli_args:
        parent_parser.add_argument(*arg["name_or_flags"], **arg["options"])

    subparsers = parser.add_subparsers(dest="command")
    train_parser = subparsers.add_parser("train", parents=[parent_parser])
    downstream_parser = subparsers.add_parser("downstream", parents=[parent_parser])
    eval_parser = subparsers.add_parser("eval", parents=[parent_parser])

    for arg in train_cli_args:
        train_parser.add_argument(*arg["name_or_flags"], **arg["options"])

    for arg in downstream_cli_args:
        downstream_parser.add_argument(*arg["name_or_flags"], **arg["options"])

    for arg in eval_cli_args:
        eval_parser.add_argument(*arg["name_or_flags"], **arg["options"])

    args = parser.parse_args()

    # Support both distributed and non-distributed training
    local_rank = os.environ.get("LOCAL_RANK")
    if local_rank is not None:
        dist.init_process_group("nccl")
        local_rank = int(local_rank)

    # Only print on rank 0
    with nullcontext() if not local_rank else redirect_stdout(None):
        args = parser.parse_args()

        if args.command is None:
            parser.print_help()
            exit(1)

        if args.sweep:
            ckpt_range = eval(f"range({args.sweep})")
            output_root = args.output
            assert output_root is not None
            print(f"Running sweep over {len(ckpt_range)} checkpoints.")

            for step in ckpt_range:
                step_output = output_root / f"step{step}"
                print(f"Running for step {step}, saving to '{step_output}'...")

                args.output = step_output
                args.revision = f"step{step}"
                lens_main(args)
        else:
            lens_main(args)


if __name__ == "__main__":
    run()
