from __future__ import annotations
import base64
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
import uuid

from fluentpy import (FluentWindow, NavigationView, Card, AccentButton, Button, TextButton,
                      LineEdit, PasswordLineEdit, TextEdit, SpinBox, DoubleSpinBox,
                      ComboBox, ToggleSwitch, ProgressBar, FluentScrollBar, FluentIcon, fluent_icon, theme)
from fluentpy.qt import QtCore, QtGui, QtWidgets, Signal
from .config import Config, Identity, VERSION, data_dir
from .network import API, normalize_url
from ..core.music import read_music, arrange, write_arrangement, render_preview
from ..core.playback import Player, Hotkeys
from ..core.hotkeys import validate_bindings
from .shortcut_edit import ShortcutEdit
from .file_drop import MusicFileDropFilter
from .note_view import NoteView

ROOT = Path(__file__).resolve().parents[2]

def label(text, size=None, muted=False, bold=False):
    w = QtWidgets.QLabel(text)
    w.setWordWrap(True)
    w.setTextFormat(QtCore.Qt.TextFormat.PlainText)
    if muted:
        w.setObjectName('muted')
    if size:
        font = w.font(); font.setPixelSize(size); font.setBold(bold); w.setFont(font)
        w.setStyleSheet(f'font-size:{size}px;font-weight:{600 if bold else 400};')
    return w

def row(*widgets):
    layout = QtWidgets.QHBoxLayout()
    layout.setSpacing(10)
    for w in widgets:
        if w is None:
            layout.addStretch(1)
        else:
            layout.addWidget(w)
    return layout

def card(title=None):
    w = Card()
    layout = QtWidgets.QVBoxLayout(w)
    layout.setContentsMargins(18,16,18,16)
    layout.setSpacing(12)
    if title:
        layout.addWidget(label(title,16,bold=True))
    return w, layout

def button(text, callback, accent=False):
    w = AccentButton(text) if accent else Button(text)
    w.clicked.connect(callback)
    return w

def table(headers):
    w = QtWidgets.QTableWidget(0,len(headers))
    w.setHorizontalHeaderLabels(headers)
    w.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
    w.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
    w.setAlternatingRowColors(False)
    w.verticalHeader().hide()
    w.horizontalHeader().setStretchLastSection(True)
    w.setShowGrid(False)
    w.setMinimumHeight(135)
    return w

def clock_text(seconds):
    seconds = max(0,int(seconds))
    return f'{seconds//60:02d}:{seconds%60:02d}'

class Bus(QtCore.QObject):
    done = Signal(int,object,object)
    message = Signal(str)
    hotkey = Signal(object)

class MainWindow(FluentWindow):
    def __init__(self, dry_run=False, config_dir=None):
        self.config = Config(config_dir)
        self.identity = Identity(self.config.directory)
        super().__init__('Fluent Melody · 口风琴演奏')
        self.resize(1200,960)
        self.setMinimumSize(970,740)
        self.set_dark(bool(self.config.values['dark']))
        theme.set_accent('#356285')
        self.dry_run = dry_run
        self.song = self.plan = None
        self.file_path = None
        self.score_epoch = 0
        self._conversion_refresh_requested = False
        self.room_code = None
        self.room = None
        self.room_generation = None
        self.room_fresh = 0.0
        self.room_unsafe = False
        self.room_sync_epoch = 0
        self.room_input_error_seen = None
        self.job_id = None
        self.job_result = None
        self._ai_attempt = None
        self.account_mp = self.account_ai = None
        self._closing = False
        self.hotkeys = None
        self._shortcut_capturing = False
        self._updating_hotkeys = False
        self._hotkey_generation = 0
        self._sequence = 0
        self._jobs = {}
        self._pending = set()
        self.pool = ThreadPoolExecutor(max_workers=5,thread_name_prefix='melody')
        self.bus = Bus(self)
        self.bus.done.connect(self._complete)
        self.bus.message.connect(self.log)
        self.bus.hotkey.connect(self.on_hotkey)
        self.player = Player(on_status=lambda value:self.bus.message.emit(str(value)),dry_run=dry_run)
        self.mp = API(self.config.values['multiplayer_url'],self.identity)
        self.ai = API(self.config.values['ai_url'],self.identity)
        self._build()
        self._apply_beta_visibility()
        self.update_shortcut_labels()
        theme.subscribe(self,self._style_tables)
        self._style_tables()
        self.setAcceptDrops(True)
        self.file_drop = MusicFileDropFilter(self,self.load_dropped_song,self.log)
        self.tick = QtCore.QTimer(self)
        self.tick.timeout.connect(self._tick)
        self.tick.start(100)
        self.net_tick = QtCore.QTimer(self)
        self.net_tick.timeout.connect(self.poll_room)
        self.net_tick.start(300)
        self.ai_tick = QtCore.QTimer(self)
        self.ai_tick.timeout.connect(self.poll_job)
        self.ai_tick.start(1200)
        if not dry_run:
            try:
                self.hotkeys = Hotkeys(lambda key:self.bus.hotkey.emit((self._hotkey_generation,key)),
                                       bindings=self.shortcut_bindings(),on_error=self.bus.message.emit)
                self.hotkeys.start()
            except Exception as exc:
                self.log(f'快捷键注册失败：{exc}')
                self.shortcut_status.setText(f'快捷键未启用：{exc}。可以在这里修改后重新应用。')
        self.log(f'已就绪。{self.start_key} 开始／停止，{self.pause_key} 暂停／继续。')
        for warning in self.config.warnings:
            self.log(warning)
        if dry_run:
            self.log('测试模式：不会向游戏发送键盘或鼠标操作。')
        if not dry_run and self.config.values['check_updates'] and self.config.values['update_url']:
            QtCore.QTimer.singleShot(1500,lambda:self.check_updates(quiet=True))

    def _build(self):
        self.content_layout.setSpacing(12)
        self.content_layout.setContentsMargins(20,16,20,16)
        brand = label('口风琴演奏',24,bold=True)
        brand.setWordWrap(False)
        self.mode_badge = label('单机演奏 · 免费',muted=True)
        edition = label(f'Fluent Melody  /  {VERSION}',muted=True)
        edition.setWordWrap(False)
        self.content_layout.addLayout(row(brand,edition,None,self.mode_badge))
        self.nav = NavigationView()
        self.nav.sidebar.set_expanded_width(172)
        self.nav.sidebar.set_expanded(True)
        for key,title,icon,builder in [
                ('solo','单机演奏',FluentIcon.HOME,self._solo_page),
                ('room','多人合奏',FluentIcon.CHAT_MULTIPLE,self._room_page),
                ('ai','AI 编曲',FluentIcon.BOT,self._ai_page),
                ('settings','设置',FluentIcon.SETTINGS,self._settings_page),
                ('about','关于',FluentIcon.INFO,self._about_page)]:
            page = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(page)
            layout.setContentsMargins(20,0,4,0); layout.setSpacing(14)
            layout.addWidget(label(title,25,bold=True))
            builder(layout)
            layout.addStretch(1)
            scroll = QtWidgets.QScrollArea(); scroll.setWidgetResizable(True)
            scroll.setVerticalScrollBar(FluentScrollBar(parent=scroll))
            scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            scroll.setWidget(page)
            self.nav.add_sub_interface(scroll,fluent_icon(icon),title,key,position='bottom' if key in ('settings','about') else 'top')
        self.content_layout.addWidget(self.nav,1)
        controls, layout = card()
        self.play_label = label('等待载入歌曲',muted=True)
        self.start_button = button('开始演奏',self.handle_f1,True)
        self.pause_button = button('暂停／继续',self.handle_f4)
        self.stop_button = button('停止',self.handle_stop)
        self.clock_label = label('00:00 / 00:00',muted=True)
        layout.addLayout(row(self.play_label,None,self.clock_label,self.start_button,self.pause_button,self.stop_button))
        self.progress = ProgressBar(); self.progress.setRange(0,1000); self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.content_layout.addWidget(controls)
        self.logs = TextEdit(); self.logs.setReadOnly(True); self.logs.setAcceptRichText(False)
        self.logs.document().setMaximumBlockCount(600); self.logs.setFixedHeight(84)
        self.content_layout.addLayout(row(label('运行日志',muted=True),None,button('清空',self.logs.clear)))
        self.content_layout.addWidget(self.logs)

    def _solo_page(self,root):
        root.addWidget(label('拖入 MIDI、NBS 即可载入，在下方选择转换方式。单机转换无需联网。',muted=True))
        box,layout = card()
        self.song_title = label('尚未载入歌曲',18,bold=True)
        self.song_info = label('支持 .mid / .midi / .nbs',muted=True)
        layout.addLayout(row(self.song_title,None,button('载入示例',self.load_example),button('打开歌曲',self.open_song,True)))
        layout.addWidget(self.song_info)
        self.notes_view = NoteView(); layout.addWidget(self.notes_view)
        root.addWidget(box)
        box,layout = card('转换与音轨')
        self.ai_convert_switch = ToggleSwitch()
        self.ai_convert_switch.setAccessibleName('AI 自动改编转换')
        self.ai_convert_switch.setToolTip('切换后会停止当前单机演奏并重新转换歌曲；选择自动保存。')
        self.ai_convert_switch.setChecked(self.config.values['ai_auto_convert'])
        self.ai_convert_hint = label('',muted=True)
        self.ai_convert_hint.setWordWrap(False)
        self._update_ai_convert_hint()
        layout.addLayout(row(label('AI 自动改编转换',bold=True),None,self.ai_convert_hint,self.ai_convert_switch))
        self.ai_convert_switch.toggled.connect(self.set_ai_auto_convert)
        self.track_mode = ComboBox(); self.track_mode.addItems(['自动选择旋律','合并所有音轨','使用勾选音轨'])
        self.speed = DoubleSpinBox(); self.speed.setRange(.5,1.5); self.speed.setSingleStep(.05)
        self.speed.setDecimals(2); self.speed.setValue(float(self.config.values['speed']))
        self.speed.setSuffix(' ×'); self.speed.setFixedWidth(100)
        layout.addLayout(row(self.track_mode,label('速度'),self.speed,None,button('重新转换',self.convert_song)))
        self.track_table = table(['选择','音轨','音符数'])
        self.track_table.setFixedHeight(144)
        self.track_table.setColumnWidth(0,55); self.track_table.setColumnWidth(1,370)
        layout.addWidget(self.track_table)
        self.conversion_info = label('每次松键后至少间隔 0.1 秒；密集段落会自动简化。',muted=True)
        layout.addWidget(self.conversion_info)
        layout.addLayout(row(button('试听前 3 分钟',self.preview),button('导出演奏版 NBS',self.export_nbs),None))
        root.addWidget(box)

    def _update_ai_convert_hint(self):
        self.ai_convert_hint.setText('已开启 · 本地模型，无需联网'
                                    if self.config.values['ai_auto_convert'] else
                                    '已关闭 · 使用普通转换规则')

    def set_ai_auto_convert(self,enabled):
        enabled = bool(enabled)
        old = self.config.values['ai_auto_convert']
        self.config.values['ai_auto_convert'] = enabled
        try:
            self.config.save()
        except OSError as exc:
            self.config.values['ai_auto_convert'] = old
            self.ai_convert_switch.blockSignals(True)
            self.ai_convert_switch.setChecked(old)
            self.ai_convert_switch.blockSignals(False)
            self._update_ai_convert_hint()
            self.log(f'转换方式未能保存，原状态已保留：{exc}')
            return
        self.ai_convert_switch.blockSignals(True)
        self.ai_convert_switch.setChecked(enabled)
        self.ai_convert_switch.blockSignals(False)
        self._update_ai_convert_hint()
        if old==enabled:
            return
        self.log('已开启 AI 自动改编转换，使用本地模型。' if enabled else
                 '已关闭 AI 自动改编转换，使用普通转换规则。')
        if self.room_code:
            if 'convert' in self._pending:
                self._conversion_refresh_requested = True
            self.log('该选项将在下次单机转换时生效。')
        elif self.song:
            self.convert_song()

    def _room_page(self,root):
        self.room_intro = label('',muted=True)
        root.addWidget(self.room_intro)
        box,layout = card('我的联机时长')
        self.mp_balance = label('未连接服务器',muted=True)
        self.mp_token = PasswordLineEdit('输入联机 token')
        layout.addWidget(self.mp_balance)
        layout.addLayout(row(self.mp_token,button('兑换',lambda:self.redeem('mp'),True),button('刷新',lambda:self.refresh_account('mp'))))
        root.addWidget(box)
        box,layout = card()
        self.room_heading = label('加入一场合奏',18,bold=True)
        self.join_code = LineEdit('六位连接码'); self.join_code.setMaxLength(6); self.join_code.setFixedWidth(155)
        self.create_button = button('创建房间',self.create_room,True)
        self.join_button = button('加入',self.join_room)
        self.leave_button = button('退出房间',self.leave_room)
        layout.addLayout(row(self.room_heading,None,self.join_code,self.join_button,self.create_button,self.leave_button))
        self.room_notice = label('房主上传歌曲后，每个人会获得自己的声部。',muted=True)
        layout.addWidget(self.room_notice)
        self.members = table(['成员','声部','状态']); self.members.setMaximumHeight(190)
        self.members.setColumnWidth(0,190); self.members.setColumnWidth(1,290)
        layout.addWidget(self.members)
        self.upload_button = button('上传歌曲…',lambda:self.upload_song(choose=True))
        self.ready_button = button('准备',self.handle_f1,True)
        layout.addLayout(row(self.upload_button,self.ready_button,None))
        self.room_shortcut_hint = label('',muted=True)
        layout.addWidget(self.room_shortcut_hint)
        root.addWidget(box)

    def _ai_page(self,root):
        root.addWidget(label('把歌曲编成独奏或多人声部。AI 服务由你在设置中选择的服务器提供。',muted=True))
        box,layout = card('AI 额度')
        self.ai_balance = label('未连接服务器',muted=True)
        self.ai_token = PasswordLineEdit('输入 AI token')
        layout.addWidget(self.ai_balance)
        layout.addLayout(row(self.ai_token,button('兑换',lambda:self.redeem('ai'),True),button('刷新',lambda:self.refresh_account('ai'))))
        root.addWidget(box)
        box,layout = card('提交编曲')
        self.ai_source = label('先在单机演奏页面载入歌曲',muted=True)
        layout.addLayout(row(self.ai_source,None,button('选择歌曲',self.open_song)))
        self.people_count = SpinBox(); self.people_count.setRange(1,8); self.people_count.setValue(2); self.people_count.setFixedWidth(80)
        layout.addLayout(row(label('演奏人数'),self.people_count,None))
        self.ai_instruction = TextEdit(); self.ai_instruction.setAcceptRichText(False)
        self.ai_instruction.setPlaceholderText('例如：保留前奏和间奏，第一人主旋律，第二人伴奏，难段尽量流畅。最多 500 字。')
        self.ai_instruction.setFixedHeight(82); layout.addWidget(self.ai_instruction)
        self.ai_status = label('等待提交',muted=True); layout.addWidget(self.ai_status)
        self.submit_button = button('开始编曲',self.submit_ai,True)
        self.revision_button = button('修改这一版',lambda:self.submit_ai(revision=True))
        self.download_button = button('下载 NBS',self.download_ai)
        layout.addLayout(row(self.submit_button,self.revision_button,self.download_button,None))
        layout.addWidget(label('每次编曲或修改的消耗由服务器决定，余额不足时不会开始。',muted=True))
        root.addWidget(box)

    def _settings_page(self,root):
        box,layout = card('快捷键')
        self.shortcut_start = ShortcutEdit(self.config.values['hotkey_start'])
        self.shortcut_pause = ShortcutEdit(self.config.values['hotkey_pause'])
        for editor in (self.shortcut_start,self.shortcut_pause):
            editor.recordingStarted.connect(self.begin_shortcut_capture)
            editor.recordingFinished.connect(self.end_shortcut_capture)
            editor.invalidShortcut.connect(self.shortcut_error)
        self.shortcut_start_label = label('开始／停止')
        self.shortcut_pause_label = label('暂停／继续')
        layout.addLayout(row(self.shortcut_start_label,None,self.shortcut_start))
        layout.addLayout(row(self.shortcut_pause_label,None,self.shortcut_pause))
        layout.addWidget(label('点击输入框后按下按键组合，例如 F8、Ctrl+Alt+P。修改后点击应用。',muted=True))
        self.shortcut_status = label('录入快捷键时会暂时停止全局快捷键监听。',muted=True)
        layout.addWidget(self.shortcut_status)
        self.apply_shortcut_button = button('应用快捷键',self.apply_shortcuts,True)
        self.reset_shortcut_button = button('恢复默认',self.reset_shortcuts)
        layout.addLayout(row(self.apply_shortcut_button,self.reset_shortcut_button,None))
        root.addWidget(box)
        box,layout = card('实验功能')
        self.beta_switch = ToggleSwitch()
        self.beta_switch.setChecked(self.config.values['beta_mode'])
        self.beta_switch.toggled.connect(self.set_beta_mode)
        layout.addLayout(row(label('Beta 模式'),None,self.beta_switch))
        layout.addWidget(label('开启后显示多人合奏、AI 编曲及服务器连接设置。',muted=True))
        self.beta_status = label('',muted=True)
        layout.addWidget(self.beta_status)
        root.addWidget(box)
        box,layout = card('连接设置')
        self.connection_card = box
        self.mp_url = LineEdit('联机服务器地址'); self.mp_url.setText(self.config.values['multiplayer_url'])
        self.ai_url = LineEdit('AI 编曲服务器地址'); self.ai_url.setText(self.config.values['ai_url'])
        self.nickname = LineEdit('合奏中显示的名字'); self.nickname.setText(self.config.values['name']); self.nickname.setMaxLength(24)
        layout.addWidget(label('联机服务器')); layout.addWidget(self.mp_url)
        layout.addWidget(label('AI 编曲服务器')); layout.addWidget(self.ai_url)
        layout.addWidget(label('昵称')); layout.addWidget(self.nickname)
        layout.addLayout(row(button('保存设置',self.save_settings,True),None))
        root.addWidget(box)
        box,layout = card('外观与更新')
        self.dark_switch = ToggleSwitch(); self.dark_switch.setChecked(bool(self.config.values['dark']))
        self.dark_switch.toggled.connect(self.change_theme)
        layout.addLayout(row(label('深色主题'),None,self.dark_switch))
        self.update_url = LineEdit('更新清单地址（可留空）'); self.update_url.setText(self.config.values['update_url'])
        layout.addWidget(self.update_url)
        layout.addLayout(row(button('保存设置',self.save_settings,True),button('检查更新',self.check_updates),None))
        root.addWidget(box)
        box,layout = card('演奏键位')
        layout.addWidget(label('Z X C V B N M ,  →  1 2 3 4 5 6 7 高音 1'))
        layout.addWidget(label('鼠标左键：低音　右键：高音　中键：升半音\n左键 + 中键：低音半音　右键 + 中键：高音半音\n每次只按一个音符键；松键后等待至少 0.1 秒，再开始下一音。',muted=True))
        self.control_help = label('',muted=True)
        layout.addWidget(self.control_help)
        root.addWidget(box)

    def _about_page(self,root):
        root.addWidget(label('Fluent Melody',24,bold=True))
        root.addWidget(label(f'口风琴演奏工具  ·  版本 {VERSION}',muted=True))
        root.addWidget(label('制作作者：性邓的小馒头'))
        root.addWidget(label('载入 MIDI / NBS 歌曲，转换为口风琴可以演奏的单音旋律。'))
        box,layout = card('自研界面 · FluentPy')
        layout.addWidget(label('本程序使用自研 UI 组件库 FluentPy。FluentPy 是基于 Qt for Python 独立实现的 Fluent 风格组件库，采用 MIT 许可证。'))
        layout.addWidget(label('FluentPy 并非其他同类型 GPLv3 开源 UI 组件库的分支、封装或换皮，也未使用这些组件库的源码、样式或资源。'))
        layout.addWidget(label('窗口、导航、按钮、输入框、开关，以及主题与动画均来自 FluentPy。',muted=True))
        layout.addLayout(row(button('查看 FluentPy 许可',lambda:self.open_notice('licenses/FluentPy-LICENSE.txt')),None))
        root.addWidget(box)
        box,layout = card('第三方依赖与许可')
        layout.addWidget(label('Qt / PySide6 提供窗口与绘图基础；图标来自 Microsoft Fluent System Icons。它们属于第三方项目，分别遵循各自的许可，不属于 FluentPy 的自研部分。'))
        layout.addWidget(label('FluentPy 的 MIT 许可不改变 Qt / PySide6 等依赖的许可要求。完整声明与许可文件随程序附带。',muted=True))
        layout.addWidget(label('本地旋律识别采用 ISMIR 2019 符号旋律模型的公开预训练权重（MIT），通过 NumPy 在本机运行。模型作者、来源和适用范围见随附说明。',muted=True))
        layout.addLayout(row(button('第三方声明',lambda:self.open_notice('licenses/THIRD_PARTY_NOTICES.md')),
                             button('模型说明',lambda:self.open_notice('assets/models/MODEL_CARD.md')),
                             button('打开许可目录',lambda:self.open_notice('licenses')),None))
        root.addWidget(box)

    def open_notice(self,relative):
        bases = [ROOT]
        if getattr(sys,'frozen',False):
            bases.extend((Path(sys.executable).parent,Path(sys.executable).parent.parent))
        for base in bases:
            path = base/relative
            if path.exists():
                if not QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path.resolve()))):
                    self.log(f'无法打开，请手动查看：{path}')
                return
        self.log('未找到许可文件，请保留完整发布包中的 licenses 目录。')

    @property
    def beta_enabled(self):
        return self.config.values['beta_mode']

    def _apply_beta_visibility(self):
        enabled = self.beta_enabled
        for route in ('room','ai'):
            self.nav.set_route_visible(route,enabled)
        self.connection_card.setVisible(enabled)
        self.beta_switch.blockSignals(True)
        self.beta_switch.setChecked(enabled)
        self.beta_switch.blockSignals(False)
        self.beta_status.setText('已开启，功能入口显示在左侧。' if enabled else '已关闭，仅显示单机演奏。')

    def set_beta_mode(self,enabled):
        enabled = bool(enabled)
        old = self.beta_enabled
        if not enabled and (self.room_code or
                            any(k!='ai_poll' and k.startswith(('room_','ai_','redeem_','account_')) for k in self._pending)):
            self._apply_beta_visibility()
            message = '请先退出房间，并等待当前提交或连接操作完成，再关闭 Beta 模式。'
            self.beta_status.setText(message); self.log(message)
            return
        self.config.values['beta_mode'] = enabled
        try:
            self.config.save()
        except OSError as exc:
            self.config.values['beta_mode'] = old
            self._apply_beta_visibility()
            self.beta_status.setText('设置未能保存，原状态已保留。')
            self.log(f'Beta 模式设置未保存：{exc}')
            return
        self._apply_beta_visibility()
        self.update_shortcut_labels()
        self.log('已开启 Beta 模式。' if enabled else '已关闭 Beta 模式。')
        if not enabled and self.job_id and not self.job_result:
            self.log('本机已暂停查询。服务器中的编曲仍会继续，重新开启 Beta 后恢复查询。')

    def require_beta(self):
        if self.beta_enabled:
            return True
        self.log('请先在设置中开启 Beta 模式。')
        return False

    def _style_tables(self):
        t = theme.tokens
        for w in self.findChildren(QtWidgets.QTableWidget):
            w.setStyleSheet(f'QTableWidget {{background:{t.surface};color:{t.text};border:1px solid {t.border};border-radius:6px;selection-background-color:{t.accent};}} QHeaderView::section {{background:{t.surface};color:{t.text_muted};border:0;padding:8px;text-align:left;}} QTableWidget::item {{padding:7px;}}')
        for w in self.findChildren(QtWidgets.QScrollArea):
            w.setStyleSheet('QScrollArea {background:transparent;border:0;} QScrollArea > QWidget > QWidget {background:transparent;}')

    def log(self,message):
        if self._closing:
            return
        text = f'{time.strftime("%H:%M:%S")}  {str(message)[:600]}'
        self.logs.append(text.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;'))

    def run_task(self,title,fn,done=None,*,key=None,quiet=False):
        if self._closing or key and key in self._pending:
            return
        self._sequence += 1; task_id = self._sequence
        self._jobs[task_id] = (title,done,key,quiet)
        if key:
            self._pending.add(key)
        def execute():
            try:
                result,error = fn(),None
            except Exception as exc:
                result,error = None,exc
            try:
                self.bus.done.emit(task_id,result,error)
            except RuntimeError:
                pass
        self.pool.submit(execute)

    def _complete(self,task_id,result,error):
        task = self._jobs.pop(task_id,None)
        if task is None or self._closing:
            return
        title,done,key,quiet = task
        self._pending.discard(key)
        if key=='convert' and self._conversion_refresh_requested:
            # A switch or conversion option changed while this worker ran.
            # Discard its result (including errors) and use the latest request.
            self._conversion_refresh_requested = False
            if not self.room_code:
                self.convert_song()
            return
        if error:
            if key=='room_poll':
                self.fail_room(str(error))
            elif not quiet:
                self.log(f'{title}失败：{error}')
            if key=='ai_submit':
                self.ai_status.setText(f'提交失败：{str(error)[:150]}')
            elif key=='convert' and not self.room_code:
                self.conversion_info.setText('转换失败，请查看日志后重试。')
                self.play_label.setText('等待重新转换')
            return
        try:
            if done:
                done(result)
        except Exception as exc:
            self.log(f'{title}失败：{exc}')
            if key in ('room_poll','room_action','room_song'):
                self.fail_room('房间返回的演奏数据无效，已停止本机演奏。')
            (self.config.directory/'last_error.txt').write_text(traceback.format_exc(),encoding='utf-8')

    def load_dropped_song(self,path):
        # Dropping a file always means local loading, independent of the page.
        if not self.room_code:
            self.nav.set_current_route('solo')
        self.load_song(path)

    def open_song(self):
        path,_ = QtWidgets.QFileDialog.getOpenFileName(self,'选择歌曲',str(self.file_path or ''),'歌曲 (*.nbs *.mid *.midi)')
        if path:
            self.load_song(path)

    def load_example(self):
        self.load_song(ROOT/'assets'/'两只老虎.nbs')

    def load_song(self,path):
        if self.room_code:
            self.log('请先退出房间再更换本地歌曲，或由房主停止后上传。')
            return
        if 'load' in self._pending or 'convert' in self._pending:
            self.log('正在读取或转换歌曲，请稍候。'); return
        self.player.stop()
        self.score_epoch += 1
        self.plan = self.song = None
        self.notes_view.plan = None
        self.play_label.setText('正在读取歌曲')
        path = Path(path)
        self.song_title.setText('正在读取…')
        def done(song):
            self.song,self.file_path = song,path
            self.song_title.setText(song.name or path.stem)
            self.song_info.setText(f'{path.name}  ·  {len(song.layers)} 条音轨  ·  {len(song.notes):,} 个音符')
            self.ai_source.setText(path.name)
            counts = Counter(n.layer for n in song.notes)
            self.track_table.setRowCount(len(song.layers))
            for i,track in enumerate(song.layers):
                check = QtWidgets.QTableWidgetItem()
                check.setFlags(check.flags()|QtCore.Qt.ItemFlag.ItemIsUserCheckable)
                check.setCheckState(QtCore.Qt.CheckState.Checked)
                self.track_table.setItem(i,0,check)
                self.track_table.setItem(i,1,QtWidgets.QTableWidgetItem(f'{i+1}. {track.name or "未命名音轨"}'))
                self.track_table.setItem(i,2,QtWidgets.QTableWidgetItem(str(counts[i])))
            self.config.values['last_file'] = str(path); self.config.save()
            self.log(f'已载入 {path.name}')
            self.convert_song()
        self.run_task('载入歌曲',lambda:read_music(path),done,key='load')

    def convert_song(self):
        if self._closing:
            return
        if not self.song:
            self.log('请先载入歌曲。'); return
        if self.room_code:
            self.log('请先退出房间再转换本地歌曲。'); return
        self.player.stop()
        self.plan = None
        self.notes_view.plan = None
        self.play_label.setText('正在转换歌曲')
        if 'convert' in self._pending:
            self._conversion_refresh_requested = True
            self.conversion_info.setText('已记下新的选择，当前任务结束后重新转换…')
            return
        selected = None
        if self.track_mode.currentIndex()==1:
            selected = list(range(len(self.song.layers)))
        elif self.track_mode.currentIndex()==2:
            selected = [i for i in range(self.track_table.rowCount()) if self.track_table.item(i,0).checkState()==QtCore.Qt.CheckState.Checked]
            if not selected:
                self.conversion_info.setText('请至少勾选一个音轨，再点击重新转换。')
                self.play_label.setText('等待选择音轨')
                self.log('请至少勾选一个音轨。'); return
        song,speed = self.song,self.speed.value()
        use_ai = self.config.values['ai_auto_convert']
        self.conversion_info.setText('正在使用本地模型转换…' if use_ai else '正在使用普通规则转换…')
        def done(plan):
            if self.room_code or self.song is not song:
                return
            if use_ai!=self.config.values['ai_auto_convert']:
                self.convert_song()
                return
            self.plan = plan
            self.notes_view.plan = plan
            self.conversion_info.setText(plan['summary'])
            self.play_label.setText(plan['name'])
            self.log(plan['summary'])
            for warning in plan.get('warnings',[])[:5]:
                self.log(warning)
        self.run_task('转换歌曲',lambda:arrange(song,players=1,selected=selected,speed=speed,gap=.1,use_ai=use_ai),done,key='convert')

    def export_nbs(self):
        if not self.plan:
            self.log('请先载入并转换歌曲。'); return
        path,_ = QtWidgets.QFileDialog.getSaveFileName(self,'导出演奏版',self.plan['name']+'_口风琴.nbs','NBS (*.nbs)')
        if path:
            plan = self.plan
            self.run_task('导出',lambda:write_arrangement(plan,path),lambda _:self.log(f'已导出 {Path(path).name}'))

    def preview(self):
        if not self.plan:
            self.log('请先转换歌曲。'); return
        plan = self.plan
        path = self.config.directory/'试听.wav'
        def done(_):
            if os.name=='nt':
                import winsound
                winsound.PlaySound(str(path),winsound.SND_FILENAME|winsound.SND_ASYNC)
            else:
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(path)))
            self.log('正在试听合成音色。')
        self.run_task('生成试听',lambda:render_preview(plan,path),done,key='preview')

    def on_hotkey(self,key):
        if self._shortcut_capturing or self._closing:
            return
        if isinstance(key,tuple):
            generation,key = key
            if generation!=self._hotkey_generation:
                return
        if key==1:
            self.handle_f1()
        elif key==4:
            self.handle_f4()

    def handle_f1(self):
        if self.room_code:
            if self.room and self.room['status'] in ('playing','countdown','paused'):
                self.log(f'合奏中请使用暂停／继续快捷键；要重新准备，请房主先停止。'); return
            member = next((m for m in (self.room or {}).get('members',[]) if m['device_id']==self.identity.device_id),{})
            self.room_action('ready',{'ready':not member.get('ready',False),'song_revision':(self.room or {}).get('song_revision',0)})
            return
        if not self.plan:
            self.log('请先载入并转换歌曲。'); return
        if self.player.state not in ('stopped','idle','finished','error'):
            self.player.stop(); return
        if os.name=='nt':
            import winsound
            winsound.PlaySound(None,0)
        self.player.play(self.plan['tracks'][0]['events'],start_at=time.time()+3)
        self.log('3 秒后开始，请切回游戏并打开口风琴。')

    def handle_f4(self):
        if self.room_code:
            if (self.room or {}).get('host_id')!=self.identity.device_id:
                self.log('由房主使用自己的暂停／继续快捷键控制全房间。'); return
            self.room_action('pause'); return
        if self.player.state=='paused':
            self.player.resume(start_at=time.time()+1)
        else:
            self.player.pause()

    def handle_stop(self):
        self.player.stop()
        if os.name=='nt':
            import winsound
            winsound.PlaySound(None,0)
        if self.room_code:
            if (self.room or {}).get('host_id')==self.identity.device_id:
                self.room_action('stop')
            else:
                self.room_action('hold')

    def change_theme(self,dark):
        self.set_dark(dark)
        self.config.values['dark'] = dark; self.config.save()

    @property
    def start_key(self):
        return self.config.values['hotkey_start']

    @property
    def pause_key(self):
        return self.config.values['hotkey_pause']

    def shortcut_bindings(self):
        return {1:self.start_key,4:self.pause_key}

    def update_shortcut_labels(self):
        start,pause = self.start_key,self.pause_key
        self.start_button.setText(('准备' if self.room_code else '开始演奏')+'  '+start)
        self.pause_button.setText('暂停／继续  '+pause)
        self.ready_button.setText('准备  '+start)
        self.room_intro.setText(f'每位成员使用自己的联机时长。按 {start} 准备，全部准备后统一倒计时。')
        self.room_shortcut_hint.setText('房主使用自己的暂停／继续快捷键控制全房间。有人掉线时，全房间暂停。')
        help_text = f'{start} 开始／停止，{pause} 暂停／继续'
        if self.beta_enabled:
            help_text += f'\n多人：{start} 准备，房主的暂停／继续键控制全房间'
        self.control_help.setText(help_text)
        self.shortcut_start_label.setText('开始／停止 · 多人准备' if self.beta_enabled else '开始／停止')
        self.shortcut_pause_label.setText('暂停／继续 · 房主控制全房间' if self.beta_enabled else '暂停／继续')

    def can_edit_shortcuts(self):
        return (self.player.state not in ('playing','countdown','paused') and
                (self.room or {}).get('status') not in ('playing','countdown','paused'))

    def shortcut_error(self,message):
        self.shortcut_status.setText(str(message))

    def begin_shortcut_capture(self):
        if not self.can_edit_shortcuts():
            self.shortcut_error('请先停止演奏，再修改快捷键。')
            focused = QtWidgets.QApplication.focusWidget()
            if focused:
                focused.clearFocus()
            return
        self._hotkey_generation += 1
        self._shortcut_capturing = True
        try:
            if self.hotkeys:
                self.hotkeys.suspend()
            self.shortcut_status.setText('正在录入。按 Esc 取消；完成后点击应用快捷键。')
        except Exception as exc:
            self.shortcut_error(f'暂时无法录入快捷键：{exc}')
            focused = QtWidgets.QApplication.focusWidget()
            if focused:
                focused.clearFocus()

    def end_shortcut_capture(self):
        self._shortcut_capturing = False
        self._hotkey_generation += 1
        if self._updating_hotkeys or self._closing:
            return
        try:
            if self.hotkeys and self.hotkeys.suspended:
                self.hotkeys.resume()
                self.hotkeys.start()
        except Exception as exc:
            self.shortcut_error(f'快捷键未启用：{exc}。请修改后重新应用。')
            self.log(f'恢复快捷键失败：{exc}')

    def reset_shortcuts(self):
        if not self.can_edit_shortcuts():
            self.shortcut_error('请先停止演奏，再修改快捷键。'); return
        self.shortcut_start.setText('F1'); self.shortcut_pause.setText('F4')
        self.apply_shortcuts()

    def apply_shortcuts(self):
        if not self.can_edit_shortcuts():
            self.shortcut_error('请先停止演奏，再修改快捷键。'); return
        try:
            proposed = validate_bindings({1:self.shortcut_start.text(),4:self.shortcut_pause.text()})
        except (ValueError,TypeError) as exc:
            self.shortcut_error(str(exc)); return
        old = self.shortcut_bindings()
        self._updating_hotkeys = True
        self._hotkey_generation += 1
        try:
            self.shortcut_start.clearFocus(); self.shortcut_pause.clearFocus()
            if self.hotkeys:
                self.hotkeys.reconfigure(proposed)
                if self.hotkeys.suspended:
                    self.hotkeys.resume()
                self.hotkeys.start()
            self.config.values.update(hotkey_start=proposed[1],hotkey_pause=proposed[4])
            self.config.save()
            self.shortcut_start.setText(proposed[1]); self.shortcut_pause.setText(proposed[4])
            self.update_shortcut_labels()
            self.shortcut_status.setText('快捷键已保存并生效。')
            self.log(f'快捷键已更新：{proposed[1]} 开始／准备，{proposed[4]} 暂停／继续。')
        except Exception as exc:
            self.config.values.update(hotkey_start=old[1],hotkey_pause=old[4])
            self.shortcut_error(f'未应用：{exc}。原设置已保留。')
            if self.hotkeys:
                try:
                    self.hotkeys.reconfigure(old)
                    if self.hotkeys.suspended:
                        self.hotkeys.resume()
                    self.hotkeys.start()
                except Exception as restore_error:
                    self.shortcut_error(f'快捷键未启用：{restore_error}。请重新选择；底部按钮仍可使用。')
            self.log(f'快捷键更新失败：{exc}')
        finally:
            self._hotkey_generation += 1
            self._shortcut_capturing = False
            self._updating_hotkeys = False

    def save_settings(self):
        if not self.beta_enabled:
            try:
                self.config.values.update(update_url=self.update_url.text().strip(),speed=self.speed.value())
                self.config.save(); self.log('已保存设置。')
            except OSError as exc:
                self.log(f'设置未保存：{exc}')
            return
        if self.room_code or self.job_id and not self.job_result:
            self.log('请先退出房间，并等待当前编曲完成，再修改服务器。'); return
        try:
            mp_url,ai_url = normalize_url(self.mp_url.text()),normalize_url(self.ai_url.text())
            name = self.nickname.text().strip() or '演奏者'
            if any(k in self._pending for k in ('account_mp','account_ai','redeem_mp','redeem_ai')):
                self.log('请等待当前连接操作完成。'); return
            self.mp.close(); self.ai.close()
            self.mp,self.ai = API(mp_url,self.identity),API(ai_url,self.identity)
            self.config.values.update(multiplayer_url=mp_url,ai_url=ai_url,name=name,
                                      update_url=self.update_url.text().strip(),speed=self.speed.value())
            self.config.save(); self.account_mp = self.account_ai = None
            self.mp_balance.setText('设置已保存，请刷新连接。'); self.ai_balance.setText('设置已保存，请刷新连接。')
            self.log('已保存服务器地址和昵称。')
        except Exception as exc:
            self.log(str(exc))

    def redeem(self,kind):
        if not self.require_beta():
            return
        api = self.mp if kind=='mp' else self.ai
        box = self.mp_token if kind=='mp' else self.ai_token
        token = box.text().strip()
        if not token:
            self.log('请先输入 token。'); return
        def done(result):
            box.clear()
            self.log('兑换成功，已绑定本机。' + ('联机时长已经开始倒计时。' if kind=='mp' else 'AI 额度已更新。'))
            self.set_account(kind,result)
        self.run_task('兑换',lambda:api.redeem(token),done,key='redeem_'+kind)

    def refresh_account(self,kind):
        if not self.require_beta():
            return
        api = self.mp if kind=='mp' else self.ai
        self.run_task('刷新账户',api.account,lambda r:self.set_account(kind,r),key='account_'+kind)

    def set_account(self,kind,result):
        if kind=='mp':
            self.account_mp = result
        else:
            self.account_ai = result
            self.ai_balance.setText(f'可用 AI 额度：{result.get("credits",0)}')

    def create_room(self):
        if not self.require_beta():
            return
        if self.room_code:
            self.log('已经在房间内。'); return
        name = self.config.values['name']
        def work():
            self.mp.sync_clock()
            return self.mp.request('POST','/rooms',{'name':name})
        self.run_task('创建房间',work,self.enter_room,key='room_join')

    def join_room(self):
        if not self.require_beta():
            return
        if self.room_code:
            self.log('请先退出当前房间。'); return
        code,name = self.join_code.text().strip(),self.config.values['name']
        if not code.isdigit() or len(code)!=6:
            self.log('请输入六位连接码。'); return
        def work():
            self.mp.sync_clock()
            return self.mp.request('POST','/rooms/join',{'code':code,'name':name})
        self.run_task('加入房间',work,self.enter_room,key='room_join')

    def enter_room(self,state):
        self.player.stop()
        self.room_code = state['code']; self.room_generation = None; self.room = None
        self.room_sync_epoch += 1
        self.room_fresh = time.monotonic(); self.room_unsafe = False
        self.mode_badge.setText('多人合奏 · '+self.room_code)
        self.update_shortcut_labels()
        self.nav.set_current_route('room')
        self.apply_room(state)
        self.log(f'已加入房间 {self.room_code}，连接延迟约 {self.mp.rtt*500:.0f} ms。')

    def room_action(self,action,data=None):
        if not self.room_code:
            return
        if self.room_unsafe:
            self.log('正在恢复连接，请等待房间同步完成。'); return
        code = self.room_code
        epoch = self.room_sync_epoch
        self.run_task('房间操作',lambda:self.mp.request('POST',f'/rooms/{code}/{action}',data or {}),
                      lambda s:self.apply_room(s,request_epoch=epoch) if self.room_code==code else None,key='room_action')

    def leave_room(self):
        if not self.room_code:
            return
        code = self.room_code
        self.player.stop()
        self.room_code = self.room = self.room_generation = None
        self.room_sync_epoch += 1
        self.mode_badge.setText('单机演奏 · 免费'); self.update_shortcut_labels()
        self.room_heading.setText('加入一场合奏'); self.members.setRowCount(0)
        self.room_notice.setText('已退出房间。'); self.log('已退出房间。')
        self.run_task('退出房间',lambda:self.mp.request('POST',f'/rooms/{code}/leave',{}),key='room_leave')

    def upload_song(self,choose=False):
        if not self.room_code or (self.room or {}).get('host_id')!=self.identity.device_id:
            self.log('请先创建房间，由房主上传歌曲。'); return
        if (self.room or {}).get('status') in ('playing','countdown'):
            self.log('请先停止合奏再上传歌曲。'); return
        if choose or not self.file_path:
            chosen,_ = QtWidgets.QFileDialog.getOpenFileName(self,'上传合奏歌曲',str(self.file_path or ''),'歌曲 (*.nbs *.mid *.midi)')
            if not chosen:
                return
            path = Path(chosen)
        else:
            path = self.file_path
        code = self.room_code
        epoch = self.room_sync_epoch
        def work():
            if path.stat().st_size > 4*1024*1024:
                raise ValueError('歌曲不能超过 4 MB。')
            return self.mp.request('POST',f'/rooms/{code}/song',{'filename':path.name,'data':base64.b64encode(path.read_bytes()).decode()})
        self.run_task('分配声部',work,lambda s:self.apply_room(s,request_epoch=epoch) if self.room_code==code else None,key='room_song')

    def poll_room(self):
        if not self.room_code or 'room_action' in self._pending or 'room_song' in self._pending:
            return
        code = self.room_code
        epoch = self.room_sync_epoch
        recovering = self.room_unsafe
        revision = (self.room or {}).get('song_revision',-1)
        def work():
            if recovering:
                self.mp.request('POST',f'/rooms/{code}/hold',{})
            return self.mp.request('GET',f'/rooms/{code}/state?song_revision={revision}')
        self.run_task('同步房间',work,lambda s:self.apply_room(s,recovered=recovering,request_epoch=epoch) if code==self.room_code else None,key='room_poll',quiet=True)

    def fail_room(self,message):
        if not self.room_code:
            return
        if not self.room_unsafe:
            self.player.stop()
            self.room_unsafe = True
            self.room_sync_epoch += 1
            self.room_generation = None
            self.room_notice.setText('连接中断，已停止本机演奏。恢复连接后将请求全房间暂停。')
            self.log(message)

    def apply_room(self,state,recovered=False,request_epoch=None):
        if not self.room_code or state.get('code')!=self.room_code:
            return
        if request_epoch is not None and request_epoch!=self.room_sync_epoch:
            return
        if self.room_unsafe and not recovered:
            return
        if self.room and (state.get('generation',0)<self.room.get('generation',0) or
                          state.get('server_time',0)<self.room.get('server_time',0)):
            return
        if 'events' not in state and self.room and state.get('song_revision')==self.room.get('song_revision'):
            state = {**state,'events':self.room.get('events',[])}
        self.room_fresh = time.monotonic(); self.room_unsafe = False
        self.room = state
        self.room_heading.setText('连接码  '+state['code'])
        self.room_notice.setText(state.get('notice') or '等待所有人准备。')
        self.members.setRowCount(len(state['members']))
        for i,member in enumerate(state['members']):
            name = member['name'] + (' · 房主' if member['device_id']==state['host_id'] else '')
            status = '离线' if not member['connected'] else ('已准备' if member['ready'] else '未准备')
            for j,text in enumerate((name,member.get('track_name','待分配'),status)):
                self.members.setItem(i,j,QtWidgets.QTableWidgetItem(text))
        is_host = state['host_id']==self.identity.device_id
        self.upload_button.setEnabled(is_host)
        generation = (state.get('generation'),state['status'])
        if generation!=self.room_generation:
            status = state['status']
            if status in ('countdown','playing') and state.get('events'):
                old = self.room_generation
                # countdown -> playing is the same scheduled performance.
                if old is None or old[0]!=generation[0] or old[1] not in ('countdown','playing'):
                    start_at = float(state.get('start_at') or state['server_time'])-self.mp.offset
                    self.player.play(state['events'],start_at=start_at,position=float(state.get('position',0)))
            elif status=='paused':
                self.player.stop()
                self.log(state.get('notice') or '全房间已暂停。')
            elif status=='waiting':
                self.player.stop()
            self.room_generation = generation
        self.play_label.setText(state.get('name') or '等待房主上传歌曲')

    def submit_ai(self,revision=False):
        if not self.require_beta():
            return
        if not self.file_path:
            self.log('请先载入歌曲。'); return
        if self.job_id and not self.job_result:
            self.log('请等待当前编曲完成。'); return
        instruction = self.ai_instruction.toPlainText().strip()
        if len(instruction)>500:
            self.log('编曲要求请控制在 500 字以内。'); return
        if revision and not self.job_result:
            self.log('请先完成一版编曲。'); return
        path,people = self.file_path,self.people_count.value()
        parent = self.job_id if revision else None
        attempt = (str(path),path.stat().st_mtime_ns,people,instruction,parent,self.ai.url)
        if self._ai_attempt and self._ai_attempt[0]==attempt:
            key = self._ai_attempt[1]
        else:
            key = uuid.uuid4().hex
            self._ai_attempt = (attempt,key)
        def work():
            if path.stat().st_size>4*1024*1024:
                raise ValueError('歌曲不能超过 4 MB。')
            self.ai.sync_clock()
            payload = {'filename':path.name,'data':base64.b64encode(path.read_bytes()).decode(),'players':people,'instruction':instruction}
            if parent:
                payload['parent_id'] = parent
            return self.ai.request('POST','/jobs',payload,headers={'Idempotency-Key':key})
        def done(result):
            self._ai_attempt = None
            self.job_id = result['id']; self.job_result = None
            self.ai_status.setText('已提交，正在排队编曲…')
            if 'credits' in result:
                self.ai_balance.setText(f'可用 AI 额度：{result["credits"]}')
        self.ai_status.setText('正在提交…')
        self.run_task('提交编曲',work,done,key='ai_submit')

    def poll_job(self):
        if not self.beta_enabled or not self.job_id or self.job_result or 'ai_submit' in self._pending:
            return
        job = self.job_id
        def done(result):
            if self.job_id!=job:
                return
            status = result['status']
            names = {'queued':'排队中…','running':'正在编曲…','completed':'编曲完成，可以下载 NBS。','failed':'编曲失败'}
            self.ai_status.setText(names.get(status,status))
            if status=='completed':
                self.job_result = result; self.log('AI 编曲完成。'); self.refresh_account('ai')
            elif status=='failed':
                self.log('AI 编曲失败：'+str(result.get('error','请查看服务器日志')))
                self.ai_status.setText('编曲失败，额度已退回。可以调整要求后重试。')
                self.job_id = None; self.refresh_account('ai')
        self.run_task('查询编曲',lambda:self.ai.request('GET',f'/jobs/{job}'),done,key='ai_poll',quiet=True)

    def download_ai(self):
        if not self.require_beta():
            return
        if not self.job_result:
            self.log('请等待编曲完成。'); return
        path,_ = QtWidgets.QFileDialog.getSaveFileName(self,'保存 AI 编曲',(self.file_path.stem if self.file_path else '编曲')+'_合奏.nbs','NBS (*.nbs)')
        if path:
            job = self.job_id
            def work():
                raw = self.ai.request('GET',f'/jobs/{job}/download',binary=True)
                target = Path(path)
                temp = target.with_suffix('.nbs.download')
                temp.write_bytes(raw)
                # Validate the downloaded format before replacing the chosen file.
                from ..core.nbs_engine import read_nbs
                read_nbs(temp)
                temp.replace(target)
            self.run_task('下载编曲',work,lambda _:self.log(f'已保存 {Path(path).name}'),key='ai_download')

    def check_updates(self,checked=False,quiet=False):
        url = self.update_url.text().strip()
        if not url:
            if not quiet:
                self.log('尚未配置更新清单地址。')
            return
        def work():
            from .updates import check_update
            return check_update(url,VERSION)
        def done(result):
            if result:
                box = QtWidgets.QMessageBox(self)
                box.setWindowTitle('发现新版本'); box.setTextFormat(QtCore.Qt.TextFormat.PlainText)
                box.setText(f'版本 {result["version"]} 已发布。\n{result["notes"]}')
                box.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Open|QtWidgets.QMessageBox.StandardButton.Cancel)
                if box.exec()==QtWidgets.QMessageBox.StandardButton.Open:
                    QtGui.QDesktopServices.openUrl(QtCore.QUrl(result['url']))
            elif not quiet:
                self.log('当前已是最新版本。')
        self.run_task('检查更新',work,done,key='update',quiet=quiet)

    def _tick(self):
        editable = self.can_edit_shortcuts()
        if not editable and self.shortcut_start.isEnabled():
            self.shortcut_status.setText('演奏期间暂不能修改快捷键，请先停止演奏。')
        elif editable and not self.shortcut_start.isEnabled():
            self.shortcut_status.setText('现在可以修改快捷键，点击输入框开始录入。')
        for widget in (self.shortcut_start,self.shortcut_pause,self.apply_shortcut_button,self.reset_shortcut_button):
            if widget.isEnabled()!=editable:
                widget.setEnabled(editable)
        if self.room and self.room.get('status') in ('countdown','playing') and self.player.error:
            failure = (self.room.get('generation'),self.player.error)
            if failure!=self.room_input_error_seen:
                self.room_input_error_seen = failure
                self.fail_room('本机演奏中断，正在暂停全房间：'+str(self.player.error))
        if self.room_code and not self.room_unsafe and time.monotonic()-self.room_fresh>1.8:
            self.fail_room('房间同步超时，已停止本机演奏。')
        if self.account_mp:
            remain = float(self.account_mp.get('expires_at') or 0)-(time.time()+self.mp.offset)
            if remain>0:
                days = int(remain//86400)
                self.mp_balance.setText(f'剩余 {days} 天 {int(remain%86400//3600):02d}:{int(remain%3600//60):02d}:{int(remain%60):02d} · 离线也会倒计时')
            else:
                self.mp_balance.setText('联机时长已到期，请兑换新的 token。')
        duration = (self.room or {}).get('duration',0) if self.room_code else (self.plan or {}).get('duration',0)
        position = self.player.position
        if self.room and self.room.get('status')=='paused':
            position = self.room.get('position',0)
        self.clock_label.setText(clock_text(position)+' / '+clock_text(duration))
        self.progress.setValue(min(1000,int(position/duration*1000)) if duration else 0)
        self.notes_view.set_position(position)
        if self.room:
            status = self.room['status']
            if status=='countdown':
                remain = max(0,math.ceil((self.room.get('start_at') or 0)-time.time()-self.mp.offset))
                self.room_notice.setText(f'{remain} 秒后开始，请切回游戏。')

    def closeEvent(self,event):
        self.file_drop.close()
        self.player.stop()
        if self.hotkeys:
            self.hotkeys.stop()
        if self.room_code:
            code = self.room_code
            self.pool.submit(lambda:self.mp.request('POST',f'/rooms/{code}/leave',{}))
        self._closing = True
        self.tick.stop(); self.net_tick.stop(); self.ai_tick.stop()
        self.pool.shutdown(wait=False,cancel_futures=True)
        self.config.values['speed'] = self.speed.value(); self.config.save()
        if os.name=='nt':
            import winsound
            winsound.PlaySound(None,0)
        event.accept()

def main():
    smoke = '--self-test' in sys.argv
    dry_run = '--dry-run' in sys.argv or smoke
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName('Fluent Melody')
    app.setFont(QtGui.QFont('Microsoft YaHei UI',10))
    # One real input owner per Windows session. Test clients can run independently.
    lock = QtCore.QLockFile(str(data_dir()/'player.lock'))
    lock.setStaleLockTime(0)
    if not dry_run and not lock.tryLock(100):
        QtWidgets.QMessageBox.information(None,'Fluent Melody','程序已经运行，请使用已打开的窗口。')
        return 0
    import tempfile
    smoke_directory = tempfile.TemporaryDirectory(prefix='melody-exe-test-') if smoke else None
    try:
        window = MainWindow(dry_run=dry_run,config_dir=smoke_directory.name if smoke else None)
    except Exception:
        detail = traceback.format_exc()
        (data_dir()/'startup_error.txt').write_text(detail,encoding='utf-8')
        QtWidgets.QMessageBox.critical(None,'启动失败','请查看本地应用数据 FluentMelody 文件夹内的 startup_error.txt。\n'+detail.splitlines()[-1])
        return 1
    window.show()
    if smoke:
        output = Path(sys.argv[sys.argv.index('--self-test')+1]).resolve()
        window.load_example()
        def finish_smoke():
            try:
                if not window.plan or not window.plan['tracks'][0]['events']:
                    raise ValueError('打包后的示例未能读取和转换')
                if fluent_icon(FluentIcon.HOME).isNull():
                    raise ValueError('打包后的 FluentPy 图标缺失')
                write_arrangement(window.plan,Path(smoke_directory.name)/'roundtrip.nbs')
                read_music(Path(smoke_directory.name)/'roundtrip.nbs')
                model_plan = arrange(read_music(ROOT/'assets'/'小星星_三音轨示例.nbs'))
                if not any('已使用本地旋律识别模型' in text for text in model_plan['warnings']):
                    raise ValueError('打包后的本地模型未能实际参与转换')
                rules_plan = arrange(read_music(ROOT/'assets'/'小星星_三音轨示例.nbs'),use_ai=False)
                if not any('AI 自动改编已关闭' in text for text in rules_plan['warnings']):
                    raise ValueError('关闭 AI 后未使用普通规则转换')
                if not window.ai_convert_switch.isVisible() or not window.ai_convert_switch.isChecked():
                    raise ValueError('主界面 AI 转换开关缺失或默认值不正确')
                window.grab().save(str(output.with_suffix('.png')))
                window.ai_convert_switch.setChecked(False)
                deadline = time.monotonic()+8
                while 'convert' in window._pending and time.monotonic()<deadline:
                    app.processEvents(); time.sleep(.01)
                if not window.plan or Config(smoke_directory.name).values['ai_auto_convert']:
                    raise ValueError('关闭 AI 后未能保存选项或重新转换')
                app.processEvents()
                window.grab().save(str(output.with_name(output.stem+'_ai_off.png')))
                window.nav.set_current_route('settings',animated=False)
                window.shortcut_start.setText('F8')
                window.shortcut_pause.setText('Ctrl+Alt+P')
                window.apply_shortcuts()
                if window.start_key!='F8' or Config(smoke_directory.name).values['hotkey_pause']!='Ctrl+Alt+P':
                    raise ValueError('快捷键设置未能保存')
                if 'Ctrl+Alt+P' not in window.pause_button.text():
                    raise ValueError('快捷键提示未更新')
                app.processEvents()
                window.grab().save(str(output.with_name(output.stem+'_settings.png')))
                if window.nav.is_route_visible('room') or window.nav.is_route_visible('ai'):
                    raise ValueError('默认状态未隐藏 Beta 功能')
                window.beta_switch.setChecked(True)
                if not window.nav.is_route_visible('ai') or not Config(smoke_directory.name).values['beta_mode']:
                    raise ValueError('Beta 模式未能开启和保存')
                app.processEvents()
                window.grab().save(str(output.with_name(output.stem+'_beta.png')))
                window.beta_switch.setChecked(False)
                window.nav.set_current_route('about',animated=False)
                app.processEvents()
                about_text = '\n'.join(w.text() for w in window.findChildren(QtWidgets.QLabel) if w.isVisible())
                if '制作作者：性邓的小馒头' not in about_text or '自研 UI 组件库 FluentPy' not in about_text:
                    raise ValueError('关于页信息缺失')
                window.grab().save(str(output.with_name(output.stem+'_about.png')))
                output.write_text(json.dumps({'ok':True,'song':window.plan['name'],'events':len(window.plan['tracks'][0]['events']),
                                               'dry_run':True,'version':VERSION,'shortcuts':window.shortcut_bindings(),
                                               'beta_default_hidden':True,'beta_persisted':True,'about_verified':True,
                                               'offline_model_verified':True,'ai_switch_verified':True,
                                               'rules_conversion_verified':True},ensure_ascii=False),encoding='utf-8')
                window.close(); app.exit(0)
            except Exception:
                output.write_text(traceback.format_exc(),encoding='utf-8')
                window.close(); app.exit(1)
        QtCore.QTimer.singleShot(3000,finish_smoke)
    return app.exec()

if __name__=='__main__':
    raise SystemExit(main())
