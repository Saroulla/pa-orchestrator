# BUILD_STATUS Verification Handover

## Context

A remote audit found that BUILD_STATUS.md marks Phase 2 steps as "done" but the actual source files may not be committed. Specifically:

- `main.py` line 21 imports `from orchestrator.maker import main as maker_main`
- No `orchestrator/maker/` directory exists on phase-2-maker-beta branch (verified via GitHub API)
- Tests exist for MAKER modules (e.g., `test_maker_router.py`, `test_classifier.py`) but the modules under test are missing
- `config/maker/` scaffold is marked done in BUILD_STATUS but not present

**App will fail to start with ImportError if MAKER module is truly missing.**

---

## Task for Local Agent

Verify the actual state of the repository on your local machine. Use the commands below to confirm or refute the remote findings.

### 1. Check for orchestrator/maker/ directory

```bash
ls -la orchestrator/ | grep -i maker
```

**Expected if missing:** No output  
**Expected if exists:** `drwxr-xr-x ... maker`

If `maker/` exists, list its contents:
```bash
find orchestrator/maker -type f -name "*.py" | head -20
```

### 2. Check for config/maker/ directory

```bash
ls -la config/ | grep -i maker
```

**Expected if missing:** No output  
**Expected if exists:** `drwxr-xr-x ... maker`

If it exists:
```bash
find config/maker -type f | head -20
```

### 3. List all adapters in orchestrator/proxy/adapters/

```bash
ls -1 orchestrator/proxy/adapters/*.py | wc -l
echo "---"
ls -1 orchestrator/proxy/adapters/
```

**What we expect:** 15 files including article_extract, brave_search, claude_api, claude_code, email_send, file_read, file_write, google_cse, http_fetch, pa_groq, pa_haiku, pdf_extract, playwright_web, template_render, wrapper_templates.

### 4. Check main.py imports

```bash
grep -n "from orchestrator.maker" orchestrator/main.py
```

**Expected output:**
```
21:from orchestrator.maker import main as maker_main
```

If this line exists but `orchestrator/maker/` doesn't, the app cannot start.

### 5. Test count vs. implementation count

```bash
echo "Test files referencing 'maker':"
ls -1 tests/test_maker*.py tests/test_classifier.py tests/test_persona.py tests/test_quota.py 2>/dev/null | wc -l

echo "---"
echo "Actual maker module files:"
find orchestrator/maker -name "*.py" 2>/dev/null | wc -l
```

**Expected if missing:** Tests = ~10, Actual = 0

### 6. Check jobs/ directory structure

```bash
find jobs -type f 2>/dev/null | head -20
echo "---"
ls -la jobs/ 2>/dev/null
```

**Expected:** Look for `jobs/maker/` subdirectory (per E1 in BUILD_STATUS).

### 7. Verify git status

```bash
git status
git branch -a
```

**Confirm you are on `phase-2-maker-beta`** and working tree is clean.

### 8. Test app startup (if safe)

```bash
cd /path/to/repo
python -c "from orchestrator.main import app; print('✓ App imports successfully')"
```

**If MAKER is missing:** Will get `ModuleNotFoundError: No module named 'orchestrator.maker'`

---

## Reporting Back

Once you've run these commands, report:

1. **Does `orchestrator/maker/` exist?** (yes/no)
   - If yes: which files are in it?
2. **Does `config/maker/` exist?** (yes/no)
3. **Adapter count:** (should be 15)
4. **Test files for MAKER:** (count)
5. **Can the app import successfully?** (yes/no)
6. **Any discrepancies between BUILD_STATUS and filesystem?**

This will determine whether the remote findings are accurate and what the next build steps actually need to be.

---

## If findings confirm remote audit

If the MAKER module and config/maker are indeed missing, then:

1. **Phase 2 is NOT 80% done** — it's more like 40% done (F1 is the only completed major step, R1–R6 tests exist but modules don't).
2. **Steps to rebuild:**
   - C1–C7: Write the missing MAKER core modules from test specs
   - A2: Create config/maker/ scaffold
   - D1–D3: Implement system messages + promotion logic
   - E1–E2: Populate jobs/maker/ and extend job_runner
   - Verify R1–R6 tests pass
   - F2–F4: Complete CTO removal and MAKER integration
   - G1–G2: Rewrite docs

This is a clean-slate opportunity if you want to simplify.
