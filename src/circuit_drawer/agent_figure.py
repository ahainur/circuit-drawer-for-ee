"""Figure task specialization of the shared editor worker and transaction protocol."""
from copy import deepcopy
import json
import time
from uuid import uuid4
from pathlib import Path

from .agent import ACTIVE, AgentTask, document_json, safe_text
from .agent_results import file_record, utc_now
from .figure_candidates import FIGURE_SYSTEM, CONTROL_FIGURE_SYSTEM, figure_selection, propose, verify


def same(context, table, profile):
    return document_json(context["table"]) == document_json(table) and context["profile"] == profile


class FigureTask(AgentTask):
    task_kind = "figure"

    def __init__(self, manager, context, prompt, start_from):
        super().__init__(manager, context, prompt, "selection" if start_from == "selection" else "current")
        self.understood_tags, self.export_formats, self.transactions = [], [], []
        self.proposals, self.export_check = [], None
        self.export_confirmed = False
        self.export_options = {}

    def status_data(self):
        actions = self.allowed()
        groups = deepcopy(self.candidates)
        current = self.manager.documents[self.document_id]
        for group in groups:
            for variant in group["variants"]:
                variant["matches_current"] = same(current, variant["preview"], variant["profile"])
                variant["allowed_actions"] = [a for a in ("adopt", "readopt") if a in actions
                    and group is groups[-1] and variant["passed"]
                    and not variant["matches_current"]]
        return {"task_kind": "figure", "selection": self.selection, "understood_tags": self.understood_tags,
                "export_formats": self.export_formats, "export_check": self.export_check,
                "candidates": groups,
                "followups": [], "capabilities": [{"id": "figure", "state": "available"}]}

    def synchronize(self, context):
        for group in self.candidates:
            state = group["state"]
            if state in ("discarded", "stale"):
                continue
            if context["file"] != group["file"]:
                group["state"] = "stale"
            elif state == "pending" and context["version"] != group["base_version"]:
                group["state"] = "stale"
            elif state in ("adopted", "undone"):
                transaction = next((t for t in reversed(self.transactions) if t["group_id"] == group["group_id"]), None)
                if transaction and same(context, transaction["after"], transaction["after_profile"]):
                    group["state"] = "adopted"
                elif transaction and same(context, transaction["before"], transaction["before_profile"]):
                    group["state"] = "undone"
                elif not transaction and same(context, group["before_table"], group["before_profile"]):
                    group["state"] = "undone"
                else:
                    group["state"] = "stale"
            group["current_version"] = context["version"]
        if self.export_check and (context["version"] != self.run_version or context["file"] != self.context["file"] or
                                  not same(context, self.context["table"], self.context["profile"])):
            self.export_check["stale"] = True

    def allowed(self):
        if self.worker_active or self.state in ACTIVE:
            return ["status", "stop"]
        actions = ["status", "regenerate", "supplement", "back_to_drawing"]
        if self.candidates:
            actions += ["discard", "revise", "retry_appearance_only"]
            group = self.candidates[-1]
            if (self.state not in ("cancelled", "failed") and group["state"] in ("pending", "adopted", "undone")
                    and any(v["passed"] for v in group["variants"])):
                actions.append("adopt" if group["state"] == "pending" else "readopt")
        if self.transactions:
            last = self.transactions[-1]
            current = self.manager.documents[self.document_id]
            if current["file"] == last["file"] and same(current, last["after"], last["after_profile"]):
                actions.append("undo_candidate")
        if self.state == "waiting_export":
            actions.append("confirm_export")
        if self.state in ("failed", "cancelled"):
            actions.append("retry_step")
        return actions

    def plan(self, *, remaining=False):
        with self.manager.lock:
            self.pending_plan = False
            self.index = 0
            self.steps = [{"index": index, "title": title, "tool": tool, "status": "todo", "detail": "",
                           "options": {}, "elapsed_ms": 0, "needs_user": tool in ("review", "adopt", "export")}
                          for index, (tool, title) in enumerate((("understand", "理解出图要求"),
                              ("candidates", "生成美化候选"), ("review", "预览对比"), ("adopt", "采用（一步撤销）")))]

    def execute(self, step):
        tool = step["tool"]
        if tool == "understand":
            from .ai.loop import extract_table
            from .regions import PALETTE
            chat = self.chat(CONTROL_FIGURE_SYSTEM if self.context["table"].get("kind") == "control" else FIGURE_SYSTEM, stage="draw")
            source = {"requests": self.requests, "drawing": self.context["table"],
                      "profile": self.context["profile"], "palette": list(PALETTE)}
            if self.selection:
                source["only_edit_fields_below_paths"] = figure_selection(self.context["table"], self.selection)
            prompt = json.dumps(source, ensure_ascii=False)
            for attempt in range(2):
                reply = chat.send(prompt)
                self.ensure_running()
                try:
                    plan = extract_table(reply)
                    if not isinstance(plan, dict) or not isinstance(plan.get("variants"), list) or not 1 <= len(plan["variants"]) <= 3:
                        raise ValueError("需要 1 至 3 个出图方案")
                    if not all(isinstance(v, dict) for v in plan["variants"]):
                        raise ValueError("方案格式无效")
                    formats = plan.get("export_formats", [])
                    if not isinstance(formats, list) or any(f not in ("tex", "pdf", "svg", "png") for f in formats):
                        raise ValueError("导出格式无效")
                    tags = plan.get("understood_tags", [])
                    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
                        raise ValueError("要求标签格式无效")
                    break
                except (ValueError, TypeError) as exc:
                    if attempt:
                        raise
                    prompt = "Repair the JSON response. " + str(exc)
            with self.manager.lock:
                self.ensure_running()
                self.title = safe_text(plan.get("title") or self.prompt)[:80]
                self.understood_tags = [safe_text(t)[:100] for t in tags[:12]]
                self.proposals, self.export_formats = plan["variants"], list(dict.fromkeys(formats))
                if self.export_formats:
                    self.steps.append({"index": 4, "title": "导出", "tool": "export", "status": "todo",
                                       "detail": "", "options": {}, "elapsed_ms": 0, "needs_user": True})
        elif tool == "candidates":
            variants = []
            scope = figure_selection(self.context["table"], self.selection) if self.selection else []
            for index, proposal in enumerate(self.proposals):
                self.ensure_running()
                try:
                    variant = propose(self.context["table"], self.context["profile"], proposal, scope=scope)
                    candidate = self.workspace.create_candidate(variant["preview"], variant["profile"])
                    variant["candidate_id"] = candidate["candidate_id"]
                except (ValueError, TypeError, KeyError, IndexError, AttributeError) as exc:
                    variant = {"passed": False, "errors": [{"code": "figure_invalid", "message": safe_text(exc)}],
                               "preview": deepcopy(self.context["table"]), "profile": self.context["profile"],
                               "guard": {"electrical": False, "parameters": False, "bindings": False, "mono": None},
                               "diff": [], "changes": [], "size_cm": None, "highlight": {"elements": [], "wires": []}}
                variants.append({**variant, "variant_id": chr(65 + index), "title": safe_text(proposal.get("title", ""))[:12],
                                 "note": safe_text(proposal.get("note", ""))[:500]})
            group = {"group_id": uuid4().hex, "number": len(self.candidates) + 1, "state": "pending",
                     "base_version": self.context["version"], "current_version": self.context["version"],
                     "file": self.context["file"], "before_table": deepcopy(self.context["table"]),
                     "before_profile": self.context["profile"], "variants": variants}
            with self.manager.lock:
                self.ensure_running()
                self.candidates.append(group)
                self.synchronize(self.manager.documents[self.document_id])
        elif tool == "review":
            with self.manager.lock:
                self.state, step["status"] = "waiting_candidate", "waiting"
                self.wait_started = time.monotonic()
        elif tool == "export":
            if not self.export_confirmed:
                report = self.workspace.validate_candidate(self.base)
                with self.manager.lock:
                    self.export_check = {"validation": report, "formats": self.export_formats,
                                         "drawing_version": self.run_version, "stale": False}
                    self.synchronize(self.manager.documents[self.document_id])
                    self.state, step["status"] = "waiting_export", "waiting"
                return
            from .editor.figure_output import request, publish_files
            started, started_at = time.monotonic(), utc_now()
            run_id = 'run-' + uuid4().hex
            folder = self.workspace.output_dir/run_id
            options = {**self.export_options, 'action':'export', 'out_dir':str(folder)}
            export = request(self.context['table'], self.context['profile'], self.context['file'],
                             folder, options, before_publish=self.ensure_running)
            destination = self.export_options.get('out_dir')
            if destination:
                destination = Path(destination).expanduser()
                if not destination.is_absolute():
                    destination = Path(self.context['file']).parent/destination
            with self.manager.lock:
                self.ensure_running()
                if destination:
                    publish_files([Path(p) for p in export['files']], destination, self.ensure_running)
                self.runs.append({'run_id':run_id, 'drawing_version':self.run_version,
                    'candidate_id':self.base, 'started_at':started_at, 'finished_at':utc_now(),
                    'duration_ms':round((time.monotonic()-started)*1000),
                    'engine':{'name':'LaTeX','where':'local','version':''}, 'engine_status':'done',
                    'metrics':[], 'waveforms':[], 'modes':[],
                    'files':[file_record(p, run_id) for p in export['files']]})
        # Adoption itself was already acknowledged through the common editor undo stack.

    def action(self, action, req):
        if action == "ack":
            return self.ack(req)
        if action not in self.allowed():
            raise ValueError("当前状态不允许这个操作，请刷新任务状态")
        if action == "stop":
            return super().action(action, req)
        if action in ("adopt", "readopt", "undo_candidate"):
            return self.prepare_figure_command(action, req)
        if action in ("discard", "back_to_drawing"):
            if self.candidates:
                self.candidates[-1]["state"] = "discarded"
            self.command = None
            self.state, self.finished = "cancelled", time.monotonic()
        elif action == "confirm_export":
            current = self.manager.documents[self.document_id]
            if self.export_check.get('stale') and not req.get('checked_current'):
                raise ValueError('图纸已改变，请重新检查')
            checked = verify(self.context['table'], current['table'], self.context['profile'], current['profile'])
            if current['file'] != self.context['file'] or checked['errors']:
                raise ValueError('图纸结构或绑定已改变，请重新生成候选')
            if not self.export_check["validation"]["ok"]:
                raise ValueError("校验未通过，先修正问题栏中的错误")
            self.export_options = {key:req[key] for key in ('formats','png_dpi','out_dir') if key in req}
            self.export_options.setdefault('formats', self.export_formats)
            self.context = deepcopy(current)
            self.base = self.workspace.create_candidate(current['table'], current['profile'])['candidate_id']
            self.run_version = current['version']
            self.export_confirmed = True
            self.launch()
        elif action == "retry_step":
            self.launch(planning=not self.steps)
        else:
            current = self.manager.documents[self.document_id]
            if self.selection and (current["file"] != self.context["file"] or
                                   not same(current, self.context["table"], self.context["profile"])):
                raise ValueError("图纸已改变，请重新选择纸面对象")
            if self.candidates and self.candidates[-1]["state"] == "pending":
                self.candidates[-1]["state"] = "discarded"
            self.command, self.export_check, self.export_confirmed = None, None, False
            text = req.get("text", "").strip()
            if action == "retry_appearance_only":
                text += "\n保留电气连接、器件参数、器件 ID 与控制绑定，仅修改允许的外观字段。"
            if text:
                self.requests.append({"text": safe_text(text), "first_step": 0})
            self.context = deepcopy(self.manager.documents[self.document_id])
            self.base = self.workspace.create_candidate(self.context["table"], self.context["profile"])["candidate_id"]
            self.run_version = self.context["version"]
            self.launch(planning=True)
        return self.status(persist=True)

    def prepare_figure_command(self, action, req):
        current = self.manager.documents[self.document_id]
        group = self.candidates[-1]
        if action == "undo_candidate":
            transaction = self.transactions[-1]
            table, profile = transaction["before"], transaction["before_profile"]
            variant_id, label = transaction["variant_id"], transaction["label"]
        else:
            if req.get("group_id") != group["group_id"]:
                raise ValueError("候选组已改变，请重新选择")
            variant = next((v for v in group["variants"] if v["variant_id"] == req.get("variant_id")), None)
            if not variant or not variant["passed"]:
                raise ValueError("此方案未通过外观检查，不能采用")
            table, profile = variant["preview"], variant["profile"]
            if same(current, table, profile):
                raise ValueError("当前图纸已经是此方案")
            checked = verify(current["table"], table, current["profile"], profile)
            if checked["errors"]:
                raise ValueError("图纸检查已改变，请重新生成候选")
            variant_id, label = variant["variant_id"], "出图 Agent · " + variant["title"]
        if self.command:
            if (self.command["version"] == current["version"] and self.command["action"] == action and
                    self.command["variant_id"] == variant_id):
                return {**self.status(), "command": deepcopy(self.command)}
            if self.command["version"] == current["version"]:
                raise ValueError("上一项采用操作尚待确认")
        self.command = {"token": uuid4().hex, "action": action, "table": deepcopy(table), "profile": profile,
                        "version": current["version"], "file": current["file"], "group_id": group["group_id"],
                        "variant_id": variant_id, "undo_count": 1, "undo_label": label,
                        "before": deepcopy(current["table"]), "before_profile": current["profile"]}
        return {**self.status(), "command": deepcopy(self.command)}

    def ack(self, req):
        token, applied, command = req.get("token"), req.get("applied"), self.command
        if token in self.acks:
            return self.status()
        if (not command or token != command["token"] or not isinstance(applied, dict) or
                applied.get("version") != command["version"] + 1 or applied.get("file") != command["file"] or
                not same(applied, command["table"], command["profile"])):
            raise ValueError("采用确认与候选不一致")
        group = self.candidates[-1]
        undoing = command["action"] == "undo_candidate"
        if undoing:
            self.transactions.pop()
            group["state"] = "undone"
        else:
            self.transactions.append({"group_id": group["group_id"], "variant_id": command["variant_id"],
                                      "before": command["before"], "before_profile": command["before_profile"],
                                      "after": command["table"], "after_profile": command["profile"],
                                      "file": command["file"], "label": command["undo_label"]})
            group["state"], group["adopted_variant"] = "adopted", command["variant_id"]
        self.acks[token], self.command = True, None
        self.context = {**self.context, **deepcopy(applied)}
        self.base = self.workspace.create_candidate(applied["table"], applied["profile"])["candidate_id"]
        self.run_version = applied["version"]
        self.export_confirmed = False
        self.export_check = None
        if not undoing:
            self.steps[2]["status"] = "done"
            self.index = 3
            self.launch()
        else:
            self.state = "done"
            self.steps[3]["status"] = "cancelled"
        self.synchronize(self.manager.documents[self.document_id])
        return self.status(persist=True)
