"""Run the plan change check once. Used by the background job installed by install.sh
(every 30 minutes). Free: no API, no Claude. Texts you only if something changed."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from imessage_plans import changes, notify  # noqa: E402
from imessage_plans.client import HelperDB, HelperError  # noqa: E402

if __name__ == "__main__":
    db = HelperDB()
    try:
        out = changes.check(db, notify.text_me)
    except HelperError as e:
        out = {"error": str(e)}
    finally:
        db.close()
    print(json.dumps(out))
