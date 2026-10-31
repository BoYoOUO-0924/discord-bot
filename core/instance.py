"""確保同一份程式只有一個 bot 在執行。

兩個 bot 同時登入時，每個指令都會被兩邊搶著處理：互相搶資料庫、超過 3 秒而出現
Unknown interaction，或是回覆兩次。用作業系統的檔案鎖判斷，程式當掉時鎖會自動釋放。
"""
import os
import sys
from typing import IO, Optional


def acquire_single_instance(lock_path: str) -> Optional[IO]:
    """成功時回傳要一直開著的檔案（關掉就會釋放鎖）；已經有另一個 bot 在執行時回傳 None。"""
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle
