from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QLabel, QProgressBar, QMessageBox, QCheckBox,
    QListWidget, QListWidgetItem, QSplitter, QDialogButtonBox,
    QComboBox, QSpinBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush

from ..models import BookMeta
from ..renamer import (
    FileNameTemplate, RenamePreviewGenerator, RenamePreviewItem,
    RegexRule, DefaultRegexRules, FileNameSanitizer
)
from .workers import RenameWorker


class RenameDialog(QDialog):
    rename_completed = pyqtSignal(int)

    def __init__(self, books: list, parent=None):
        super().__init__(parent)
        self._books = books
        self._preview_items: list = []
        self._regex_rules: list = DefaultRegexRules.get_default_rules()
        self._template = FileNameTemplate()
        self._rename_worker = None

        self.setWindowTitle("批量重命名")
        self.setMinimumSize(900, 650)
        self._init_ui()
        self._refresh_preview()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Orientation.Vertical)

        top_widget = QGroupBox("命名模板与规则")
        top_layout = QVBoxLayout(top_widget)

        template_layout = QFormLayout()
        self.template_edit = QLineEdit(FileNameTemplate.DEFAULT_TEMPLATE)
        self.template_edit.textChanged.connect(self._on_template_changed)
        template_layout.addRow("文件名模板:", self.template_edit)

        placeholder_label = QLabel(
            "可用占位符: {title} 书名 | {author} 作者 | {publisher} 出版社 | "
            "{publish_year} 出版年份 | {format} 文件格式 | {edition} 版本"
        )
        placeholder_label.setStyleSheet("color:#666;font-size:11px")
        placeholder_label.setWordWrap(True)
        template_layout.addRow("", placeholder_label)

        top_layout.addLayout(template_layout)

        regex_group = QGroupBox("正则替换规则")
        regex_layout = QVBoxLayout(regex_group)

        self.rule_list = QListWidget()
        self.rule_list.itemChanged.connect(self._on_rule_toggled)
        self._populate_rule_list()
        regex_layout.addWidget(self.rule_list)

        btn_row = QHBoxLayout()
        add_rule_btn = QPushButton("添加规则")
        add_rule_btn.clicked.connect(self._add_rule)
        btn_row.addWidget(add_rule_btn)

        edit_rule_btn = QPushButton("编辑选中")
        edit_rule_btn.clicked.connect(self._edit_selected_rule)
        btn_row.addWidget(edit_rule_btn)

        remove_rule_btn = QPushButton("删除选中")
        remove_rule_btn.clicked.connect(self._remove_selected_rule)
        btn_row.addWidget(remove_rule_btn)

        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._reset_rules)
        btn_row.addWidget(reset_btn)

        btn_row.addStretch()
        regex_layout.addLayout(btn_row)

        top_layout.addWidget(regex_group)

        splitter.addWidget(top_widget)

        preview_group = QGroupBox("预览")
        preview_layout = QVBoxLayout(preview_group)

        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("font-weight:bold")
        preview_layout.addWidget(self.stats_label)

        self.preview_table = QTableWidget()
        self.preview_table.setColumnCount(4)
        self.preview_table.setHorizontalHeaderLabels(
            ["状态", "原文件名", "新文件名", "备注"]
        )
        header = self.preview_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.preview_table.setAlternatingRowColors(True)
        preview_layout.addWidget(self.preview_table)

        splitter.addWidget(preview_group)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        layout.addWidget(splitter)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

        btn_box = QHBoxLayout()
        self.pause_btn = QPushButton("⏸ 暂停")
        self.pause_btn.setVisible(False)
        self.pause_btn.clicked.connect(self._toggle_pause)
        btn_box.addWidget(self.pause_btn)

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self._cancel_rename)
        btn_box.addWidget(self.cancel_btn)

        btn_box.addStretch()

        self.rename_btn = QPushButton("✅ 开始重命名")
        self.rename_btn.setStyleSheet(
            "QPushButton{background:#2ecc71;color:white;border:none;border-radius:4px;padding:8px 24px;font-weight:bold;font-size:13px}"
            "QPushButton:hover{background:#27ae60}"
            "QPushButton:disabled{background:#bdc3c7}"
        )
        self.rename_btn.clicked.connect(self._start_rename)
        btn_box.addWidget(self.rename_btn)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_box.addWidget(close_btn)

        layout.addLayout(btn_box)

    def _populate_rule_list(self):
        self.rule_list.clear()
        for rule in self._regex_rules:
            item = QListWidgetItem()
            text = f"{rule.description}" if rule.description else rule.pattern
            if not rule.enabled:
                text = f"[已禁用] {text}"
            item.setText(text)
            item.setCheckState(
                Qt.CheckState.Checked if rule.enabled else Qt.CheckState.Unchecked
            )
            item.setData(Qt.ItemDataRole.UserRole, rule)
            self.rule_list.addItem(item)

    def _on_template_changed(self):
        self._template = FileNameTemplate(self.template_edit.text())
        self._refresh_preview()

    def _on_rule_toggled(self, item):
        rule = item.data(Qt.ItemDataRole.UserRole)
        if rule:
            rule.enabled = (item.checkState() == Qt.CheckState.Checked)
            text = f"{rule.description}" if rule.description else rule.pattern
            if not rule.enabled:
                text = f"[已禁用] {text}"
            item.setText(text)
            self._refresh_preview()

    def _add_rule(self):
        dialog = RegexRuleDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rule = dialog.get_rule()
            if rule:
                self._regex_rules.append(rule)
                self._populate_rule_list()
                self._refresh_preview()

    def _edit_selected_rule(self):
        current = self.rule_list.currentItem()
        if not current:
            return
        rule = current.data(Qt.ItemDataRole.UserRole)
        if not rule:
            return

        dialog = RegexRuleDialog(self, rule)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_rule = dialog.get_rule()
            if new_rule:
                idx = self._regex_rules.index(rule)
                self._regex_rules[idx] = new_rule
                self._populate_rule_list()
                self._refresh_preview()

    def _remove_selected_rule(self):
        current = self.rule_list.currentRow()
        if current >= 0:
            self._regex_rules.pop(current)
            self._populate_rule_list()
            self._refresh_preview()

    def _reset_rules(self):
        reply = QMessageBox.question(
            self, "确认", "确定要恢复默认正则规则吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._regex_rules = DefaultRegexRules.get_default_rules()
            self._populate_rule_list()
            self._refresh_preview()

    def _refresh_preview(self):
        generator = RenamePreviewGenerator(self._template, self._regex_rules)
        self._preview_items = generator.generate_preview(self._books)
        self._update_preview_table()
        self._update_stats()

    def _update_preview_table(self):
        self.preview_table.setRowCount(len(self._preview_items))

        for i, item in enumerate(self._preview_items):
            status_item = QTableWidgetItem()
            if item.error:
                status_item.setText("❌ 错误")
                status_item.setForeground(QBrush(QColor("#e74c3c")))
            elif item.has_conflict:
                status_item.setText("⚠️ 冲突")
                status_item.setForeground(QBrush(QColor("#f39c12")))
            elif item.will_change:
                status_item.setText("✅ 将重命名")
                status_item.setForeground(QBrush(QColor("#2ecc71")))
            else:
                status_item.setText("— 不变")
                status_item.setForeground(QBrush(QColor("#999")))
            status_item.setFlags(status_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.preview_table.setItem(i, 0, status_item)

            original_item = QTableWidgetItem(item.original_name)
            original_item.setFlags(original_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.preview_table.setItem(i, 1, original_item)

            new_item = QTableWidgetItem(item.new_name)
            if item.will_change and not item.has_conflict and not item.error:
                new_item.setForeground(QBrush(QColor("#2ecc71")))
            elif item.has_conflict:
                new_item.setForeground(QBrush(QColor("#f39c12")))
            elif item.error:
                new_item.setForeground(QBrush(QColor("#e74c3c")))
            new_item.setFlags(new_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.preview_table.setItem(i, 2, new_item)

            note = ""
            if item.error:
                note = item.error
            elif item.has_conflict:
                note = f"与 {len(item.conflict_with)} 个文件重名"
            remark_item = QTableWidgetItem(note)
            remark_item.setFlags(remark_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.preview_table.setItem(i, 3, remark_item)

    def _update_stats(self):
        total = len(self._preview_items)
        will_change = sum(1 for i in self._preview_items if i.will_change and not i.has_conflict and not i.error)
        conflicts = sum(1 for i in self._preview_items if i.has_conflict)
        errors = sum(1 for i in self._preview_items if i.error)

        self.stats_label.setText(
            f"共 {total} 个文件 | 将重命名: {will_change} | 冲突: {conflicts} | 错误: {errors}"
        )

        can_rename = will_change > 0 and conflicts == 0 and errors == 0
        self.rename_btn.setEnabled(can_rename)

    def _start_rename(self):
        items_to_rename = [
            i for i in self._preview_items
            if i.will_change and not i.has_conflict and not i.error
        ]
        if not items_to_rename:
            return

        reply = QMessageBox.question(
            self, "确认重命名",
            f"确定要重命名 {len(items_to_rename)} 个文件吗？\n\n"
            "重命名操作将在后台执行，如果任何文件失败，所有已重命名的文件将自动回滚。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.rename_btn.setEnabled(False)
        self.pause_btn.setVisible(True)
        self.cancel_btn.setVisible(True)
        self.progress.setVisible(True)
        self.progress.setRange(0, len(items_to_rename))
        self.progress.setValue(0)

        self._rename_worker = RenameWorker(items_to_rename)
        self._rename_worker.progress.connect(self._on_rename_progress)
        self._rename_worker.finished_signal.connect(self._on_rename_finished)
        self._rename_worker.paused_changed.connect(self._on_paused_changed)
        self._rename_worker.start()

    def _on_rename_progress(self, current: int, total: int, name: str):
        self.progress.setValue(current)
        self.status_label.setText(f"正在重命名: {name}")

    def _on_rename_finished(self, result):
        self.progress.setVisible(False)
        self.pause_btn.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.rename_btn.setEnabled(True)

        if result.success:
            self.status_label.setText(
                f"✅ 重命名完成！共成功 {result.renamed} 个文件"
            )
            QMessageBox.information(
                self, "完成",
                f"成功重命名 {result.renamed} 个文件。"
            )
            self.rename_completed.emit(result.renamed)
            self._refresh_preview()
            self.accept()
        else:
            if result.rolled_back:
                self.status_label.setText(f"❌ 重命名失败，已全部回滚: {result.error_message}")
                QMessageBox.warning(
                    self, "失败",
                    f"重命名失败，已自动回滚所有更改。\n\n原因: {result.error_message}"
                )
            else:
                self.status_label.setText(f"⚠️ 部分失败: 成功 {result.renamed}, 失败 {result.failed}")

    def _toggle_pause(self):
        if not self._rename_worker:
            return
        if self._rename_worker.is_paused():
            self._rename_worker.resume()
        else:
            self._rename_worker.pause()

    def _on_paused_changed(self, paused: bool):
        self.pause_btn.setText("▶️ 继续" if paused else "⏸ 暂停")

    def _cancel_rename(self):
        if self._rename_worker:
            reply = QMessageBox.question(
                self, "确认取消",
                "确定要取消重命名吗？已重命名的文件将自动回滚。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._rename_worker.cancel()


class RegexRuleDialog(QDialog):
    def __init__(self, parent=None, rule: RegexRule = None):
        super().__init__(parent)
        self._editing = rule is not None
        self.setWindowTitle("编辑正则规则" if self._editing else "添加正则规则")
        self.setMinimumWidth(400)
        self._init_ui(rule)

    def _init_ui(self, rule: RegexRule = None):
        layout = QFormLayout(self)

        self.desc_edit = QLineEdit()
        if rule:
            self.desc_edit.setText(rule.description)
        self.desc_edit.setPlaceholderText("规则描述（可选）")
        layout.addRow("描述:", self.desc_edit)

        self.pattern_edit = QLineEdit()
        if rule:
            self.pattern_edit.setText(rule.pattern)
        self.pattern_edit.setPlaceholderText("例如: 【[^】]+】")
        layout.addRow("正则表达式:", self.pattern_edit)

        self.replace_edit = QLineEdit()
        if rule:
            self.replace_edit.setText(rule.replacement)
        self.replace_edit.setPlaceholderText("替换为...")
        layout.addRow("替换为:", self.replace_edit)

        self.enabled_check = QCheckBox("启用此规则")
        if rule:
            self.enabled_check.setChecked(rule.enabled)
        else:
            self.enabled_check.setChecked(True)
        layout.addRow("", self.enabled_check)

        test_group = QGroupBox("测试")
        test_layout = QFormLayout(test_group)
        self.test_input = QLineEdit()
        self.test_input.setPlaceholderText("输入测试文本...")
        self.test_input.textChanged.connect(self._update_test)
        test_layout.addRow("输入:", self.test_input)

        self.test_output = QLineEdit()
        self.test_output.setReadOnly(True)
        test_layout.addRow("输出:", self.test_output)

        layout.addRow(test_group)

        btn_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addRow(btn_box)

    def _update_test(self):
        try:
            import re
            pattern = self.pattern_edit.text()
            replacement = self.replace_edit.text()
            text = self.test_input.text()
            if pattern:
                result = re.sub(pattern, replacement, text)
                self.test_output.setText(result)
                self.test_output.setStyleSheet("color:#2ecc71")
            else:
                self.test_output.setText(text)
        except re.error as e:
            self.test_output.setText(f"正则错误: {e}")
            self.test_output.setStyleSheet("color:#e74c3c")

    def get_rule(self) -> RegexRule:
        return RegexRule(
            pattern=self.pattern_edit.text(),
            replacement=self.replace_edit.text(),
            enabled=self.enabled_check.isChecked(),
            description=self.desc_edit.text(),
        )
