"""评测入口：按数据集名称创建实验，或遍历全部数据集。"""

from mentor.benchmarks.experiment import run_experiment
from mentor.benchmarks.dataset import get_all_datasets


def run_benchmark(dataset_name: str):
    # 实验名带数据集名，便于在 Langfuse 中按数据集筛选。
    exp_name = f"实验数据集:{dataset_name}"
    description = f"对实验数据集{dataset_name}的离线评估"
    return run_experiment(exp_name, dataset_name, description)


def run_all_benchmarks():
    # 逐个数据集创建实验；单个数据集失败会向上抛，不吞异常。
    datasets = get_all_datasets()
    return [run_benchmark(ds.name) for ds in datasets]
