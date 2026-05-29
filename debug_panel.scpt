-- debug_panel.scpt: 找到右侧面板并递归展开
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

			-- 步骤1: 扫描 → 识别文本
			click menu item "识别文本" of menu "扫描" of menu bar item "扫描" of menu bar 1
			log "已点击 扫描 → 识别文本"
			delay 10

			-- 步骤2: 找到包含"文字识别"或"识别文本"标题的容器
			log "=== 查找右侧面板容器 ==="
			-- 尝试在 scroll area 中查找
			try
				set allScrolls to every scroll area of targetWin
				log "找到 " & (count of allScrolls) & " 个 scroll area"
				repeat with sa in allScrolls
					try
						set saElems to entire contents of sa
						set foundCount to 0
						repeat with e in saElems as list
							try
								set n to name of e
								set d to description of e
								if (n contains "识别") or (d contains "识别") then
									log "  [scroll] class=" & (class of e) & " name='" & n & "' desc='" & d & "'"
									set foundCount to foundCount + 1
								end if
							end try
						end repeat
						log "  scroll area 中找到 " & foundCount & " 个识别相关元素"
					end try
				end repeat
			end try

			-- 尝试在 UI element 子元素中查找
			log "=== 深度遍历窗口子元素 ==="
			my deepSearch(targetWin, "", 0)
		end tell
	end tell
end run

on deepSearch(parentElem, prefix, depth)
	if depth > 3 then return
	tell application "System Events"
		tell process "PDF Expert"
			try
				set children to every UI element of parentElem
				set childCount to (count of children)
				repeat with c in children
					try
						set n to name of c
						set cl to class of c
						if (n contains "识别") or (n contains "语言") or (n contains "中文") or (n contains "文字") or (n contains "应用") then
							log prefix & "[" & depth & "] class=" & cl & " name='" & n & "'"
						end if
						if depth < 3 then
							my deepSearch(c, prefix & "  ", depth + 1)
						end if
					end try
				end repeat
			end try
		end tell
	end tell
end deepSearch
