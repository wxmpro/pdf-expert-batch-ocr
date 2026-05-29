-- full_dump.scpt: 完整输出所有元素
on run argv
	set filePath to item 1 of argv
	set baseName to do shell script "basename " & quoted form of filePath & " | sed 's/\\.pdf$//'"

	do shell script "open " & quoted form of filePath
	delay 5
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

			click menu item "识别文本" of menu "扫描" of menu bar item "扫描" of menu bar 1
			delay 12

			log "========================================"
			log "=== 窗口 entire contents 全部元素 ==="
			log "========================================"
			set allElems to entire contents of targetWin
			set totalCount to 0
			repeat with e in allElems as list
				try
					set eClass to class of e
					set eName to name of e
					set eDesc to description of e
					set eValue to ""
					try
						set eValue to value of e
					end try

					if eName is missing value then set eName to "(无名称)"
					if eDesc is missing value then set eDesc to "(无)"
					if eValue is missing value then set eValue to "(无)"

					log "[" & totalCount & "] class=" & eClass & " | name=" & eName & " | desc=" & eDesc & " | value=" & eValue
					set totalCount to totalCount + 1
				end try
			end repeat
			log "=== 共 " & totalCount & " 个元素 ==="

			log ""
			log "========================================"
			log "=== 窗口直接子元素（第1层）==="
			log "========================================"
			try
				set children to every UI element of targetWin
				set childIdx to 0
				repeat with c in children
					try
						set cClass to class of c
						set cName to name of c
						set cDesc to description of c
						if cName is missing value then set cName to "(无名称)"
						if cDesc is missing value then set cDesc to "(无)"
						log "  L1[" & childIdx & "] class=" & cClass & " | name=" & cName & " | desc=" & cDesc

						-- 递归看第2层
						try
							set grandchildren to every UI element of c
							set gIdx to 0
							repeat with gc in grandchildren
								try
									set gcClass to class of gc
									set gcName to name of gc
									set gcDesc to description of gc
									if gcName is missing value then set gcName to "(无名称)"
									if gcDesc is missing value then set gcDesc to "(无)"
									if (gcName is not "(无名称)") or (gcDesc is not "(无)") then
										log "    L2[" & gIdx & "] class=" & gcClass & " | name=" & gcName & " | desc=" & gcDesc
									end if
								end try
								set gIdx to gIdx + 1
								if gIdx > 60 then exit repeat
							end repeat
						end try

						set childIdx to childIdx + 1
						if childIdx > 40 then exit repeat
					end try
				end repeat
			end try
		end tell
	end tell
end run
