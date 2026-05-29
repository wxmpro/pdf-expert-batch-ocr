-- pdfexpert_ocr.scpt
-- PDF Expert OCR automation via AppleScript UI Scripting
-- Operates on a copy (original file is never modified)
-- Arguments: argv[1]=file_path, argv[2]="keep" (optional, batch mode keeps PDF Expert open)
on run argv
	set filePath to item 1 of argv
	set baseName to do shell script "basename " & quoted form of filePath & " | sed 's/\\.pdf$//'"
	set keepApp to false
	try
		if (count of argv) > 1 and (item 2 of argv) is "keep" then
			set keepApp to true
		end if
	end try

	-- 1. 后台打开文件（避免窗口最大化）
	do shell script "open -g " & quoted form of filePath
	delay 4

	-- 2. 激活 PDF Expert
	tell application "PDF Expert" to activate
	delay 1

	tell application "System Events"
		tell process "PDF Expert"
			-- 3. 处理可能的系统弹窗（"PDF Expert 想访问其他 App 的数据"）
			try
				repeat with w in (every window)
					try
						set wName to name of w
						if wName contains "PDF Expert" or wName contains "数据" then
							-- 点击"允许"或"不允许"
							try
								if exists button "允许" of w then
									click button "允许" of w
								end if
							end try
							try
								if exists button "不允许" of w then
									click button "不允许" of w
								end if
							end try
						end if
					end try
				end repeat
			end try

			-- 4. 确保前台状态（关键！）
			set frontmost to true
			delay 0.5

			-- 5. 定位窗口
			set targetWin to missing value
			repeat with w in (every window)
				try
					if name of w contains baseName then
						set targetWin to w
						exit repeat
					end if
				end try
			end repeat
			if targetWin is missing value then
				delay 3
				repeat with w in (every window)
					try
						if name of w contains baseName then
							set targetWin to w
							exit repeat
						end if
					end try
				end repeat
			end if
			if targetWin is missing value then
				return "failed: 窗口未找到"
			end if

			-- 6. 鲁棒进入扫描模式并点击"识别..."
			-- 问题场景：①用户切到其他应用 ②扫描模式已启用导致再次点击变成取消
			-- 解决：先检测是否已在扫描模式，确保前台，必要时重试
			set scanModeOk to false
			set scanRetry to 0
			repeat while (not scanModeOk) and (scanRetry < 3)
				-- 6a. 强制确保 PDF Expert 在最前面（应对用户切换应用）
				tell application "PDF Expert" to activate
				delay 0.5
				set frontmost to true
				delay 0.5

				-- 6b. 检测扫描面板是否已显示（"识别..."按钮是否存在）
				set scanPanelVisible to false
				try
					repeat with e in (entire contents of targetWin) as list
						try
							if (class of e is button) and (name of e is "识别...") then
								set scanPanelVisible to true
								exit repeat
							end if
						end try
					end repeat
				end try

				if scanPanelVisible then
					-- 扫描模式已启用，直接点击"识别..."
					repeat with e in (entire contents of targetWin) as list
						try
							if (class of e is button) and (name of e is "识别...") then
								click e
								set scanModeOk to true
								exit repeat
							end if
						end try
					end repeat
				else
					-- 扫描模式未启用，通过菜单进入
					try
						click menu bar item "扫描" of menu bar 1
						delay 1
						click menu item "识别文本" of menu "扫描" of menu bar item "扫描" of menu bar 1
						delay 2

						-- 检查"识别..."按钮是否出现
						repeat with e in (entire contents of targetWin) as list
							try
								if (class of e is button) and (name of e is "识别...") then
									click e
									set scanModeOk to true
									exit repeat
								end if
							end try
						end repeat
					on error
						-- 菜单点击失败，可能应用不在前台
						delay 1
					end try
				end if

				if not scanModeOk then
					set scanRetry to scanRetry + 1
					delay 2
				end if
			end repeat

			if not scanModeOk then
				return "failed: 无法进入扫描模式（重试" & scanRetry & "次）"
			end if

			delay 2

			-- 9. 处理确认对话框
			try
				set sheetList to every sheet of targetWin
				if (count of sheetList) > 0 then
					set dlg to item 1 of sheetList
					if exists radio button "所有全部" of dlg then
						click radio button "所有全部" of dlg
						click button "应用" of dlg
					end if
				end if
			end try

			-- 10. 等待 OCR 完成
			-- 策略A：sheet 中有 progress indicator，消失即完成（小文件）
			-- 策略B：sheet 消失后，检测"识别..."按钮恢复启用（大文件）
			set waited to 0
			set minWaitAfterSheetGone to 10  -- sheet消失后至少等10秒让OCR开始
			set sheetGoneAt to -1
			repeat while waited < 3600
				try
					set sheetList to every sheet of targetWin
					if (count of sheetList) > 0 then
						-- sheet 还在（小文件模式）
						try
							if not (exists progress indicator 1 of (item 1 of sheetList)) then
								exit repeat
							end if
						on error
							-- progress indicator 访问异常（已消失）
							exit repeat
						end try
					else
						-- sheet 已消失（大文件模式）
						if sheetGoneAt < 0 then
							set sheetGoneAt to waited
						end if
						-- sheet 消失后至少等 minWaitAfterSheetGone 秒，避免误判
						if (waited - sheetGoneAt) >= minWaitAfterSheetGone then
							-- 检测"识别..."按钮是否存在且启用
							set foundEnabledBtn to false
							repeat with e in (entire contents of targetWin) as list
								try
									if (class of e is button) and (name of e is "识别...") then
										if enabled of e is true then
											set foundEnabledBtn to true
											exit repeat
										end if
									end if
								end try
							end repeat
							if foundEnabledBtn then
								exit repeat
							end if
						end if
					end if
				end try
				delay 1
				set waited to waited + 1
			end repeat

			-- 11. 鲁棒退出扫描模式
			-- 先检测扫描面板是否仍显示，如果是才点击退出
			set scanPanelStillVisible to false
			try
				repeat with e in (entire contents of targetWin) as list
					try
						if (class of e is button) and (name of e is "识别...") then
							set scanPanelStillVisible to true
							exit repeat
						end if
					end try
				end repeat
			end try

			if scanPanelStillVisible then
				-- 扫描面板仍在，需要退出扫描模式
				set exitRetry to 0
				set exitOk to false
				repeat while (not exitOk) and (exitRetry < 3)
					try
						set frontmost to true
						delay 0.3
						click menu bar item "扫描" of menu bar 1
						delay 0.5
						click menu item "识别文本" of menu "扫描" of menu bar item "扫描" of menu bar 1
						delay 2
						set exitOk to true
					on error
						set exitRetry to exitRetry + 1
						delay 1
					end try
				end repeat
			end if

			-- 12. 保存（每秒检测窗口标题无 *，更快发现保存完成）
			keystroke "s" using command down
			set waitStart to current date
			repeat while ((current date) - waitStart) < 300
				delay 1
				try
					set winName to name of targetWin
					if (characters 1 thru 1 of winName as string) is not "*" then exit repeat
				end try
			end repeat

			-- 13. 关闭窗口
			keystroke "w" using command down
			delay 1
		end tell
	end tell

	-- 14. 单文件模式：关闭 PDF Expert
	if not keepApp then
		delay 2
		try
			tell application "PDF Expert" to quit
		end try
		-- 轮询检测进程是否退出，先温和等待，最后才强制
		set quitWaited to 0
		repeat while quitWaited < 30
			delay 1
			set isRunning to do shell script "pgrep -x 'PDF Expert' | wc -l | tr -d ' '"
			if isRunning is "0" then exit repeat
			set quitWaited to quitWaited + 1
		end repeat
		-- 30秒后还在运行，先发送 SIGTERM
		if isRunning is not "0" then
			do shell script "pkill -15 -x 'PDF Expert' 2>/dev/null || true"
			delay 5
			set isRunning to do shell script "pgrep -x 'PDF Expert' | wc -l | tr -d ' '"
			-- 最后手段：SIGKILL
			if isRunning is not "0" then
				do shell script "pkill -9 -x 'PDF Expert' 2>/dev/null || true"
			end if
		end if
	end if

	return "success"
end run
