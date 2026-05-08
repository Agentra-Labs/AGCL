# Beginner's guide — getting openslock running

This is a step-by-step walkthrough for someone who has never set up a
Python project before. If you can open a terminal and copy-paste, you
can get this running. Nothing here assumes prior experience.

You will need about 15 minutes and an internet connection.

> **Where you are:** start here.
> Once you've finished this guide, jump to whichever doc fits your goal:
> - **[configuration.md](configuration.md)** — what every setting does
>   (the friendly reference)
> - **[advanced_guide.md](advanced_guide.md)** — set up the recursive
>   multi-agent feature with real models
> - **[training.md](training.md)** — what happens when the recursive
>   feature trains on a question
> - **[recursive_mas_setup.md](recursive_mas_setup.md)** — terse
>   technical reference
> - **[main.md](main.md)** — per-file code reference

---

## What openslock does, in one paragraph

You type a question. A tiny model on your computer fires off the first
few words instantly so you see something on screen right away. Then a
big cloud model (Claude or ChatGPT) takes over and finishes the answer.
The result feels much faster than waiting for the cloud model alone.
There is also a research feature called RecursiveMAS — that's optional
and covered at the end.

---

## What you need first

1. **A computer** running macOS, Linux, or Windows. (Windows works too;
   commands look slightly different — use PowerShell or WSL.)
2. **Python 3.10 or newer.** Check by opening a terminal and running:

       python3 --version

   If you see something below 3.10, or "command not found", install
   Python from <https://www.python.org/downloads/>. On macOS, you can
   also use Homebrew (`brew install python`).
3. **An API key** from either Anthropic (Claude) or OpenAI. You'll get
   this in step 4 below. Both companies give a small amount of free
   credit to start.
4. **About 200 MB of disk space** for one small model file.

That's it.

---

## Step 1 — Open a terminal in the project folder

If you got the code as a zip, unzip it somewhere you'll remember (e.g.
your `Desktop`). Then open a terminal and `cd` into that folder:

    cd ~/Desktop/openslock

You should see files like `main.py`, `readme.md`, and a folder called
`openslock/`. If `ls` (or `dir` on Windows) shows those, you're in the
right place.

---

## Step 2 — Make a Python "virtual environment"

A virtual environment is just a private folder where Python packages
get installed for this project only — so it doesn't mess with anything
else on your computer. Make one called `env`:

    python3 -m venv env

Now turn it on:

- macOS / Linux:    `source env/bin/activate`
- Windows (PowerShell):  `.\env\Scripts\Activate.ps1`

You should see `(env)` appear at the start of your prompt. That means
you're inside the virtual environment. Anything you `pip install` from
now on stays in this `env` folder.

(If you ever want to leave it, type `deactivate`. To come back later,
re-run the activate command above.)

---

## Step 3 — Install the dependencies

One command:

    pip install -r requirements.txt

This will download a few packages: a web framework, the local-model
runner, the cloud SDKs, and PyTorch. It'll take a couple of minutes.
You'll see a lot of scrolling text — that's normal.

If it finishes without a red error, you're good.

---

## Step 4 — Get an API key

Pick **one** of these two providers. (You can add the other later.)

### Option A — Claude (Anthropic)

1. Go to <https://console.anthropic.com/>
2. Sign up (or log in) with your email.
3. Click **Settings → API Keys → Create Key**.
4. Copy the key. It starts with `sk-ant-...`. **You won't see it again
   after closing the popup**, so paste it somewhere safe right away.

### Option B — ChatGPT (OpenAI)

1. Go to <https://platform.openai.com/api-keys>
2. Sign up (or log in).
3. Click **Create new secret key**.
4. Copy the key. It starts with `sk-...`.

Treat the key like a password. Don't paste it into chat apps, don't
post screenshots of it, and don't commit it to git.

---

## Step 5 — Save the key in a `.env` file

In the project folder, there is a file called `.env.example`. Copy it
to `.env` (note the dot at the start — it's hidden by default in some
file managers):

    cp .env.example .env

(Windows PowerShell:  `Copy-Item .env.example .env`)

Open `.env` in any text editor and paste your key after the `=`:

    OPENAI_API_KEY=
    ANTHROPIC_API_KEY=sk-ant-paste-yours-here

Save the file. Only fill in the line for the provider you signed up
for; leave the other blank.

The `.env` file is automatically ignored by git, so it won't end up in
a public repo if you push your code somewhere. Never commit it.

---

## Step 6 — Download a small local model

The local model is what fires off those first few words while the
cloud catches up. It's a `.gguf` file — a special compressed format.

Make a `models/` folder if there isn't one, then download something
small. A good first pick is **SmolLM2-135M** (about 100 MB):

1. Go to <https://huggingface.co/HuggingFaceTB/SmolLM2-135M-Instruct-GGUF>
2. Click the **Files and versions** tab.
3. Click any file ending in `.Q4_K_M.gguf` (or `.Q2_K.gguf` for an
   even smaller one). Click the **download** arrow on the right.
4. Move the downloaded file into the `models/` folder of this project.

Now tell openslock where it is. Open `.env` again and add a line:

    LOCAL_MODEL_PATH=models/SmolLM2-135M.Q2_K.gguf

(Replace the filename with whatever you actually downloaded.)

If you'd rather not deal with this, openslock will still run — it just
won't have a fast first-words feature. But the model file is tiny,
worth grabbing.

---

## Step 7 — Run it

Make sure your virtual environment is still active (you should see
`(env)` in your prompt). Then:

    python main.py

The first time, it'll print "[server] not running, starting..." and
take a few seconds to load the local model. After that you'll see:

    Agent CLI — session=default  (ctrl+c to quit)

    you:

Type a question. Hit enter. You should see a grey `[local 80ms]`
followed by the local model's first few words, then the rest streams
in from the cloud.

Press **Ctrl+C** to quit when you're done.

---

## That's it. You're running.

A few good things to try:

    you: what's a good way to learn python?
    you: write a haiku about rain
    you: explain backprop in two sentences

To save your conversations to a named session that persists between
runs:

    python main.py --session work

To force a specific cloud provider:

    python main.py --provider claude
    python main.py --provider openai

---

## Things that commonly go wrong

**`command not found: python3`** — Python isn't installed. See "What
you need first" above.

**`ModuleNotFoundError`** — your virtual environment isn't active. Run
the activate command from step 2 again. You should see `(env)` in your
prompt before running anything.

**Cloud says "401 Unauthorized" or "invalid API key"** — the key in
`.env` is wrong, expired, or has no remaining credit. Double-check
you copied the whole key, including the `sk-ant-` or `sk-` prefix.
Check your account billing page.

**Local model outputs gibberish** like `heiz heiz heiz...` — the model
file isn't an instruction-tuned chat model. Either grab a different
file (look for one labeled "Instruct" or "Chat" on HuggingFace), or
add this to your `.env`:

    PROMPT_FORMAT=plain

**First request after a long pause is slow** — that's expected. When
nothing happens for 3 minutes, the local model gets unloaded to save
memory. The next request reloads it. Subsequent ones are fast again.

**You changed `.env` but it's still using the old value** — restart
the program. `.env` is read once at startup.

---

## Optional — try the recursive multi-agent feature

This is a separate, more capable part of the project that chains
several models together to think in steps. There are two ways to set
it up.

### Option A — one-shot canonical setup

Fastest. Installs the canonical pair (Qwen 0.5B + TinyLlama 1.1B),
sequential pattern, 2 rounds:

    python main.py --autoconfig

(Add `--install-deps` if you don't already have the `transformers`
package; it'll pip-install for you.)

That:
- creates a `mas.json` config file with two HuggingFace models
- adds the right settings to your `.env`
- creates `models/hf/` (where models will cache, kept inside the
  project so it's easy to clean up later)
- runs a self-test (should print `21/21 tests passed`)

### Option B — interactive wizard

Walks you through every choice (pattern, agent count, models, roles,
where to download). Uses plain prompts — no special UI to learn.

    python main.py --config

Pick this if you want non-default models, or you want to try a
different collaboration pattern (`moe`, `distill`, `deliberation`),
or you want every model fully on disk instead of in HF cache.

The wizard is documented step-by-step in **[configuration.md](configuration.md)**.

### Then run it

After either option:

    python main.py recursive info                                  # shows current config
    python main.py recursive run "explain quantum entanglement"    # one-shot
    python main.py recursive run                                   # interactive multi-turn

The first run on a topic will take a minute or two — the system asks
the cloud once for a polished answer, then trains the small projection
MLPs between agents to reproduce it locally. Subsequent questions on
similar topics reuse those trained weights from disk and skip training.

If this is interesting, **[training.md](training.md)** explains
exactly what's happening when you see those `[stage A]`/`[stage B]`
lines fly by.

When you're ready to set up your own multi-agent system with real
HuggingFace models, the next walkthrough is
**[advanced_guide.md](advanced_guide.md)** — same step-by-step style
as this guide, picks up where this one ends.

---

## Where things live

You probably won't need to touch any of these as a beginner, but for
when you're curious:

- `.env` — your API keys (you fill this in)
- `models/` — your downloaded `.gguf` and HF model files
- `.agent_state/` — your saved chat sessions and trained MAS topics
  (auto-created)
- `mas.json` — your recursive multi-agent recipe (created by
  `--config` or `--autoconfig`)
- `openslock/config.py` — defaults for everything tunable
- `docs/configuration.md` — every config knob explained simply
- `docs/training.md` — how the recursive feature auto-trains
- `docs/advanced_guide.md` — picking models for the recursive feature
- `docs/recursive_mas_setup.md` — terse technical reference
- `docs/main.md` — what every code file does
- `readme.md` — the short technical overview

---

## When you're stuck

1. Re-read the error message slowly. Most of them say what's missing.
2. Check the **"Things that commonly go wrong"** section above.
3. Make sure `(env)` is in your prompt before running commands.
4. Make sure your `.env` file actually has the key in it (open it and
   look — typos are the most common cause of "auth failed" errors).
5. If `pip install` failed, try upgrading pip first:
   `pip install --upgrade pip`, then retry `pip install -r requirements.txt`.

You're not breaking anything by trying — the worst case is you delete
the `env/` folder and start step 2 over. Nothing else on your computer
is touched.
