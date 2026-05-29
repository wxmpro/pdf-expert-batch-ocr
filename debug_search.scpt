-- debug_search.scpt: 深入查找"识别..."按钮
on run argv
	set filePath to item 1 of argv
	set baseName to do shell script "basename " & quoted form of filePath & " | sed 's/\\.pdf$//'"

	do shell script "open " & quoted form of filePath
	delay 3
	activate application "PDF Expert"
	delay 3

	tell application "System Events"
		tell process "PDF Expert"
			-- 定位窗口
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
			delay 5

			-- 查找所有包含"识别"的元素
			log "=== entire contents ==="
			set allElems to entire contents of targetWin
			repeat with e in allElems as list
				try
					set n to name of e
					set d to description of e
					if (n contains "识别") or (d contains "识别") then
						log "  class=" & (class of e) & " name='" & n & "' desc='" & d & "'"
					end if
				end try
			end repeat

			-- 遍历 group
			log "=== groups ==="
			try
				set allGroups to every group of targetWin
				repeat with g in allGroups
					try
						set gElems to entire contents of g
						repeat with e in gElems as list
							try
								set n to name of e
								set d to description of e
								if (n contains "识别") or (d contains "识别") then
									log "  class=" & (class of e) & " name='" & n & "' desc='" & d & "'"
								end if
							end try
						end repeat
					end try
				end repeat
			end try

			-- 遍历 splitter group
			log "=== splitter groups ==="
			try
				set allSGs to every splitter group of targetWin
				repeat with sg in allSGs
					try
						set sgElems to entire contents of sg
						repeat with e in sgElems as list
							try
								set n to name of e
								set d to description of e
								if (n contains "识别") or (d contains "识别") then
									log "  class=" & (class of e) & " name='" & n & "' desc='" & d & "'"
								end if
							end try
						end repeat
					end try
				end repeat
			end try

			-- 查找所有静态文本（面板中的语言和识别状态）
			log "=== 语言/识别相关所有元素 ==="
			repeat with e in allElems as list
				try
					set n to name of e
					set d to description of e
					set v to ""
					try
						set v to value of e
					end try
					set c to class of e
					if (n contains "识别") or (n contains "语言") or (n contains "中文") or (n contains "English") or (d contains "语言") or (d contains "识别") or (v contains "中文") or (v contains "English") then
						log "  class=" & c & " name='" & n & "' desc='" & d & "' value='" & v & "'"
					end if
				end try
			end repeat
		end tell
	end tell
end run
