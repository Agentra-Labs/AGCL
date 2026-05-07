# Beginner's guide — getting openslock running

This is a step-by-step walkthrough for someone who has never set up a
Python project before. If you can open a terminal and copy-paste, you
can get this running. Nothing here assumes prior experience.

You will need about 15 minutes and an internet connection.

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

This is a separate, more experimental part of the project that chains
several models together to think in steps. It's documented in
[recursive_mas_setup.md](recursive_mas_setup.md), but here's the
30-second version:

    python main.py recursive validate    # makes sure everything works
    python main.py recursive info        # shows current config
    python main.py recursive run "explain quantum entanglement simply"

Out of the box it uses two copies of your local `.gguf` model. To
configure different agents, see the linked doc above.

---

## Where things live

You probably won't need to touch any of these as a beginner, but for
when you're curious:

- `.env` — your API keys (you fill this in)
- `models/` — your downloaded `.gguf` model files
- `.agent_state/` — your saved chat sessions (auto-created)
- `openslock/config.py` — defaults for everything tunable
- `docs/main.md` — what every code file does
- `docs/recursive_mas_setup.md` — the multi-agent feature
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
