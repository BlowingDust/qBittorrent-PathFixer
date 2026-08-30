from __future__ import annotations

import hashlib
from collections import defaultdict

from .errors import ValidationFailedError
from .models import PlannedOperation
from .validator import ValidatedNamespace


def _operation_id(kind: str, source: str, target: str, sequence: int) -> str:
    raw = f"{sequence}\0{kind}\0{source}\0{target}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:20]


def compile_plan(validated: ValidatedNamespace) -> tuple[PlannedOperation, ...]:
    if validated.errors:
        raise ValidationFailedError("校验存在阻断错误，不能生成执行计划")
    operations: list[PlannedOperation] = []
    current = {int(entry["index"]): entry["path"] for entry in validated.loaded.snapshot["files"]}
    folder_reason = {
        intent.old_path: intent.record_id
        for intent in validated.loaded.intents
        if intent.item_type == "folder" and intent.action == "rename"
    }

    for source in sorted(validated.folder_mappings, key=lambda path: (-path.count("/"), path)):
        target = validated.folder_mappings[source]
        affected = [index for index, path in current.items() if path.startswith(source + "/")]
        if not affected:
            raise ValidationFailedError(f"文件夹操作没有后代文件：{source}")
        sequence = len(operations) + 1
        operations.append(
            PlannedOperation(
                operation_id=_operation_id("rename_folder", source, target, sequence),
                kind="rename_folder",
                source_path=source,
                target_path=target,
                temporary=False,
                file_index=None,
                reason_record_ids=(folder_reason[source],),
            )
        )
        for index in affected:
            current[index] = target + current[index][len(source):]

    file_intents = {
        intent.file_index: intent
        for intent in validated.loaded.intents
        if intent.item_type == "file" and intent.action == "rename" and intent.file_index is not None
    }
    pending = {
        index: validated.final_by_index[index]
        for index in file_intents
        if current[index] != validated.final_by_index[index]
    }
    temp_counter = 0
    all_final = set(validated.final_by_index.values())

    while pending:
        occupants: dict[str, set[int]] = defaultdict(set)
        for index, path in current.items():
            occupants[path].add(index)
        ready = [
            index
            for index, target in pending.items()
            if not (occupants.get(target, set()) - {index})
        ]
        if ready:
            index = min(ready)
            source = current[index]
            target = pending.pop(index)
            intent = file_intents[index]
            sequence = len(operations) + 1
            operations.append(
                PlannedOperation(
                    operation_id=_operation_id("rename_file", source, target, sequence),
                    kind="rename_file",
                    source_path=source,
                    target_path=target,
                    temporary=False,
                    file_index=index,
                    reason_record_ids=(intent.record_id,),
                )
            )
            current[index] = target
            continue

        index = min(pending)
        source = current[index]
        parent, separator, _ = source.rpartition("/")
        while True:
            temp_counter += 1
            name = f".__btfr_tmp_{index}_{temp_counter}__"
            target = f"{parent}/{name}" if separator else name
            if target not in occupants and target not in all_final:
                break
        sequence = len(operations) + 1
        operations.append(
            PlannedOperation(
                operation_id=_operation_id("rename_file", source, target, sequence),
                kind="rename_file",
                source_path=source,
                target_path=target,
                temporary=True,
                file_index=index,
                reason_record_ids=(file_intents[index].record_id,),
            )
        )
        current[index] = target

    if current != validated.final_by_index:
        mismatches = sum(current[index] != validated.final_by_index[index] for index in current)
        raise ValidationFailedError(f"操作计划内存模拟失败，{mismatches} 个文件未达到最终路径")
    return tuple(operations)
