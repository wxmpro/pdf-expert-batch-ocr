-- debug_lang.scpt: 查找语言 radio button
on run argv
	set filePath to item 1 of argv
	set baseName to do shell script "basename " & quoted form of filePath & " | sed 's/\\.pdf$//'"

	do shell script "open " & quoted form of filePath
	delay 3
	activate application "PDF Expert"
	delay 3

	tell application "System Events"
		tell process "PDF Expert"
			set targetWin to false
			repeat with w in (every window)
				try
					if name of w contains baseName then
						set targetWin to w
						exit repeat
					end if
				end try
			end repeat
			if targetWin is false then
				log "窗口未找到"
				return
			end if

			-- 进入扫描模式
			click menu item "识别文本" of menu "扫描" of menu bar item "扫描" of menu bar 1
			delay 8

			-- 查找所有 radio button
			log "=== RADIO BUTTONS ==="
			set allElems to entire contents of targetWin
			repeat with e in allElems as list
				try
					if class of e is radio button then
						log "  name='" & (name of e) & "' desc='" & (description of e) & "'"
					end if
				end try
			end repeat

			-- 查找所有包含语言名称的元素
			log "=== 语言名相关 ==="
			repeat with e in allElems as list
				try
					set n to name of e
					if (n contains "中文") or (n contains "普通话") or (n contains "英语") or (n contains "English") or (n contains "德语") or (n contains "French") or (n contains "日语") then
						log "  class=" & (class of e) & " name='" & n & "'"
					end if
				end try
			end repeat

			-- 查找 '识别...' 和 '识别' 按钮（不带省略号的）
			log "=== 识别相关按钮 ==="
			repeat with e in allElems as list
				try
					if class of e is button then
						set n to name of e
						if (n contains "识别") then
							log "  name='" & n & "' desc='" & (description of e) & "'"
						end if
					end if
				end try
			end repeat
		end tell
	end tell
end run
