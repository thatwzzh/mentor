"""Report Benchmark quality for one or all Langfuse datasets."""

import math
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv


SCORE_THRESHOLD = 0.8
SCORE_NAME = "总体相似度"
EXPERIMENT_NAME_PREFIX = "实验数据集:"


def test_benchmark_regression():
    # Langfuse SDK 和模型客户端会从进程环境读取配置，需在导入项目模块前加载。
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    from mentor.benchmarks.benchmark import run_all_benchmarks, run_benchmark

    dataset_name = os.environ.get("BENCHMARK_DATASET", "").strip()
    assert dataset_name, "请运行 make test-benchmark DS=all 或 DS=<数据集名称>"

    if dataset_name == "all":
        results = run_all_benchmarks()
        reports = [
            _summarize_benchmark_result(
                result.name.removeprefix(EXPERIMENT_NAME_PREFIX), result
            )
            for result in results
        ]
    else:
        reports = [
            _summarize_benchmark_result(dataset_name, run_benchmark(dataset_name))
        ]

    _print_report(reports)
    failed_datasets = [report["dataset"] for report in reports if not report["passed"]]
    if not reports:
        pytest.fail("没有可测试的数据集", pytrace=False)
    if failed_datasets:
        pytest.fail(
            f"{len(failed_datasets)} 个数据集未达标：{', '.join(failed_datasets)}",
            pytrace=False,
        )


def _summarize_benchmark_result(dataset_name, result):
    from mentor.benchmarks.dataset import get_items

    items = get_items(dataset_name)
    scores = []
    passed_items = 0
    failed_cases = []
    completed_ids = set()

    for index, row in enumerate(result.item_results, start=1):
        case_id = getattr(row.item, "id", index)
        case_name = _case_name(row.item, index)
        completed_ids.add(case_id)
        if row.output is None:
            failed_cases.append(f"{case_name}: Agent 没有输出")
            continue

        matches = [e.value for e in row.evaluations if e.name == SCORE_NAME]
        if len(matches) != 1:
            failed_cases.append(f"{case_name}: {SCORE_NAME} 评分缺失或重复")
            continue

        score = matches[0]
        if (
            not isinstance(score, (int, float))
            or isinstance(score, bool)
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            failed_cases.append(f"{case_name}: {SCORE_NAME} 评分无效 ({score!r})")
            continue

        scores.append(score)
        if score >= SCORE_THRESHOLD:
            passed_items += 1
        else:
            failed_cases.append(f"{case_name}: {SCORE_NAME} {score:.3f}")

    for item in items:
        if item.id not in completed_ids:
            failed_cases.append(f"{_case_name(item, item.id)}: 实验未完成")

    average = sum(scores) / len(scores) if scores else None
    passed = (
        bool(items)
        and len(result.item_results) == len(items)
        and len(scores) == len(items)
        and passed_items == len(items)
        and average is not None
        and average >= SCORE_THRESHOLD
    )
    if not items:
        failed_cases.append("数据集没有测试用例")

    return {
        "dataset": dataset_name,
        "total": len(items),
        "passed_items": passed_items,
        "average": average,
        "passed": passed,
        "failed_cases": failed_cases,
        "url": result.dataset_run_url,
    }


def _case_name(item, fallback):
    case_id = getattr(item, "id", fallback)
    content = getattr(item, "input", None)
    if content is None:
        return str(case_id)
    preview = " ".join(str(content).split())
    if len(preview) > 60:
        preview = f"{preview[:60]}…"
    return f"{case_id}（{preview}）"


def _print_report(reports):
    print(f"\nBenchmark 汇总（{SCORE_NAME} 门槛：{SCORE_THRESHOLD:.3f}）")
    if not reports:
        print("没有可测试的数据集")
        return

    for report in reports:
        average = report["average"]
        average_text = f"{average:.3f}" if average is not None else "无有效评分"
        status = "达标" if report["passed"] else "未达标"
        failed_count = report["total"] - report["passed_items"]
        print(
            f"- {report['dataset']}：{status}，平均分 {average_text}；"
            f"用例 {report['total']} 条，达标 {report['passed_items']} 条，"
            f"未达标 {failed_count} 条"
        )
        for case in report["failed_cases"]:
            print(f"    - {case}")
        if report["url"]:
            print(f"  实验详情：{report['url']}")

    passed_datasets = sum(report["passed"] for report in reports)
    total_items = sum(report["total"] for report in reports)
    passed_items = sum(report["passed_items"] for report in reports)
    print(
        f"总计：数据集 {len(reports)} 个，达标 {passed_datasets} 个，"
        f"未达标 {len(reports) - passed_datasets} 个；"
        f"用例 {total_items} 条，达标 {passed_items} 条，"
        f"未达标 {total_items - passed_items} 条"
    )
