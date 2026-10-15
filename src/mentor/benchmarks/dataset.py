from typing import List

from langfuse import get_client
from langfuse.api import Dataset

langfuse = get_client()


def get_all_datasets():
    page = 1
    datasets: List[Dataset] = []
    while True:
        result = langfuse.api.datasets.list(page=page, limit=100)
        datasets.extend(result.data)
        if page >= result.meta.total_pages:
            break
        page += 1

    return datasets


def get_items(dataset_name: str):
    dataset = langfuse.get_dataset(dataset_name)
    return dataset.items
