# Content Radar: never run out of content ideas

A free setup that watches GitHub and a few newsletters every morning, keeps only what fits your niche, notices which things *stay* hot, and lets Claude pick your next post from that list. It works for any niche that has tools, repos or launches people talk about.

---

## What it does, in plain words

1. **Every morning** (about one minute, on GitHub's free servers) it reads:
   - GitHub Trending (what's getting stars today)
   - GitHub search (new repos in your topics)
   - star counts for everything it's tracking
   - your chosen newsletters (any links to GitHub repos)
2. **It throws out anything outside your niche** using a word list you control.
3. **It watches over days.** One hot day means nothing. Hot Monday and still climbing by Wednesday? It moves up.
4. **It doubles down when sources agree.** Trending on GitHub *and* linked in a newsletter = top of the list.
5. **When you ask Claude for an idea**, Claude reads the shortlist, skips what you've already posted, scores the rest, and gives you three ideas with hooks.
6. **It keeps score.** Every post goes in a ledger with its plays, so later picks lean toward what worked for you.

---

## What you need

- A **GitHub account** (free).
- **GitHub Actions minutes:** free. A public repo has unlimited minutes. A private repo gets 2,000 free minutes a month, and this uses about 30 to 60.
- **Claude Code** on your computer.
- **Python 3.11 or newer** and **git** on your computer (Claude Code can install them for you if you ask).
- Optional: the **GitHub CLI** (`gh`), only if you want to run the collector on your own computer.

No API keys. No paid services. The Action uses GitHub's built-in token.

---

## The files

| File | What it is |
|---|---|
| `radar.py` | The collector and the commands. One file, standard Python only. |
| `sources.toml` | What it reads: trending pages, search queries, newsletter feeds. |
| `keywords.txt` | Your niche filter. **This is the file you'll edit most.** |
| `ledger.csv` | Your posts and their plays. Starts empty. |
| `.github/workflows/radar.yml` | The daily schedule. |
| `SKILL.md` | The Claude skill that turns the radar into three ideas. |

---

## Setup (about 15 minutes)

### Part A: the daily collector on GitHub

1. At the top of this repo, click **Use this template → Create a new repository**. Name it `content-radar`. Public or private both work.
2. In your new repo, go to **Settings → Actions → General → Workflow permissions**, choose **Read and write permissions** and click **Save**. This lets the daily run save its results.
3. Check your new repo has `radar.py`, `sources.toml`, `keywords.txt`, `ledger.csv`, `SKILL.md` and the `.github/workflows/radar.yml` file. Everything is already in place.
4. Open the **Actions** tab. If GitHub asks, click **I understand my workflows, go ahead and enable them**.
5. Click **radar** on the left, then **Run workflow**. Wait about a minute.
6. Go back to the repo's front page. You should see a new `RADAR.md` file and a `data` folder. Open `RADAR.md`. That's your first radar.

From now on it runs by itself every day at 09:17 UTC.

### Part B: tune it to your niche

7. **Edit `keywords.txt`** (pencil icon on GitHub). Replace the example words with your niche:
   - `[core 2 4]` is the words that *are* your niche. 2 points each, 4 at most.
   - `[outcome 2 4]` is what your audience wants: money, customers, a job, time back, passing a class.
   - `[broad 1 1]` is loosely related words. 1 point at most.
   - `[avoid -2]` is too technical or off-topic for your audience. Minus 2 each.
   - `[block]` drops a repo no matter what.
   - `[installable 0]` and `[free 0]` give no points. They only add a tag Claude can use.

   A repo scoring **3 or more** gets tracked and shown. You can add your own `[name points cap]` groups.
8. **Edit `sources.toml`:**
   - `searches`: swap the `topic:` words for your niche. To find good topics, open a few repos you like and look at the tags under their description.
   - `trending`: add a language page if your niche lives in one (for example `https://github.com/trending/rust?since=daily`).
   - `[[feed]]` blocks: add newsletters that **link to GitHub repos** and have a public RSS feed. Most Substack newsletters have one at `<name>.substack.com/feed`. Only GitHub links inside the feed are counted.
   - Small niche? Lower `star_floor` from 100 to about 30, so smaller repos can have a hot day.

### Part C: connect Claude

9. On your computer, open a terminal in the folder where you keep projects and run:
   ```
   git clone https://github.com/<your-username>/content-radar
   ```
10. Install the skill: create the folder `~/.claude/skills/content-radar/` (on Windows that's `C:\Users\<you>\.claude\skills\content-radar\`) and copy `SKILL.md` from your cloned folder into it.
11. Open `SKILL.md` and change the one line under **Your radar folder** to the folder you cloned in step 9.
12. Start Claude Code and say: **"give me a content idea"**.

---

## How it decides

### A "hot day"

A repo has a hot day when **any** of these is true:

- It's on a GitHub Trending page that day.
- It gained at least 100 stars, or 1% of its stars, that day (whichever is bigger, so giant repos don't stay hot forever).
- A newsletter mentioned it, and that newsletter hadn't mentioned it in the previous 7 days.

Search results never make a day hot. They only help the radar find things.

### The status ladder

| Status | Rule | What it means for you |
|---|---|---|
| `new` | First hot day was today or yesterday | Fresh. Only worth it if it's real news you can post in 3 days. |
| `watching` | Older than that, but not hot enough on enough days yet | Wait and see. |
| `sustained` | 2+ days old, hot today or yesterday, hot on 2 of the last 3 days, and growth hasn't halved from its peak | **The sweet spot.** It's not a one-day spike. |
| `sustained+` | Sustained **and** two kinds of source agree (Trending + a newsletter), or two newsletters | **Doubles down.** Strongest signal on the radar. |
| `peaked` | 3 days without a hot day, or growth fell under half its peak two days running | Too late for a news angle. Hidden. |
| `dead` | 7 days without a hot day | Gone. |

A `~` in front of a status means the radar has under 3 days of history, so it's still a rough guess. Give it three mornings.

### The Monday to Wednesday rule, worked through

- **Monday:** a repo hits Trending. Status `new`.
- **Tuesday:** off Trending, but it gained 400 stars on 6,000. Still a hot day. Still `new`.
- **Wednesday:** back on Trending, growth still near its peak. Hot on 3 of 3 days, 2 days old: **`sustained`**.
- If a newsletter linked it on Tuesday too, that's two kinds of source agreeing: **`sustained+`**.
- If Tuesday and Wednesday were both quiet instead, it stays `watching`, then turns `peaked` on Thursday.

### Reading `RADAR.md`

Each row is `key|status|age|hot3|stars|v1|v3|src|tags|desc`:

- `age`: days since its first hot day
- `hot3`: hot days out of the last 3
- `v1` / `v3`: stars per day (yesterday to today, and the 3-day average)
- `src`: hot days per source in the last week. `T` is Trending, `N` is newsletters (names in brackets).
- `tags`: which keyword groups matched

At most 25 rows, so Claude reads it in seconds.

---

## Asking for an idea

Say any of these in Claude Code:

- "give me a content idea"
- "what should I post"
- "check the radar"

Claude will pull the latest radar, shortlist the `sustained+`, `sustained` and news-like `new` rows, check each one's README, skip anything you've already posted (from your ledger), score the rest, and give you **three ideas**. Each comes with a hook, who it's for, the evidence, and the risk.

---

## The ledger: how it learns what works for you

Two moments, two commands. You can also just tell Claude, "log this post".

1. **When you post** something from the radar:
   ```
   python radar.py ship 2026-12-01-bughunter gh:owner/repo --angle "free bug bounty skill" --rubric 7
   ```
   The first word is any name for the post. The key is the `gh:owner/repo` from the radar row. For a topic that wasn't on the radar, use any short label as the key.
2. **About 72 hours later**, look up its plays (or views) and log them:
   ```
   python radar.py plays 2026-12-01-bughunter 18400
   ```

Once you have 3 or more posts with plays, each new post is marked:

- `breakout`: 3x or more the median of your last 10 posts
- `flop`: under half that median
- `ok`: anything in between

What the ledger changes:

- Tags that broke out for you get a small boost when the radar orders its rows.
- A past **breakout** that gets hot again shows up under **RERUN**. Make it again with a new angle.
- A **flop** never shows up again.
- Anything you posted in the last 14 days is hidden, so you're not offered it twice.

After logging, push so the GitHub copy has it: `git add ledger.csv`, `git commit -m "ledger"`, `git pull --rebase`, `git push`. Or just ask Claude to do it.

---

## Troubleshooting and FAQ

**The Action failed with a red X.**
Open the run and read the `BAD` lines. Trending and star counts are the only parts that fail the run, and it still saves everything else first. A failing newsletter just shows as `FAIL rss:<name>` on the radar.

**"Permission denied" or 403 when the Action pushes.**
Go to Settings → Actions → General → Workflow permissions and choose **Read and write permissions**.

**The radar is nearly empty.**
Your keywords are too narrow, or it's day one. Lower `track_min` in `sources.toml` to 2, add words to `[core]`, and give it three days.

**The radar is full of junk.**
Add the junk words to `[avoid]` or `[block]`, or raise `track_min` to 4.

**`STALE` at the top.**
The daily run hasn't happened in 36 hours. Check the Actions tab. GitHub sometimes pauses scheduled workflows on quiet repos. Click **Enable workflow** if you see it, then **Run workflow**.

**A newsletter shows `FAIL`.**
It either blocked GitHub's servers (some Substack feeds do) or it stopped publishing. Swap it for another, or run `python radar.py collect` on your own computer once (needs `gh auth login` first), then push.

**GitHub Trending changed its layout and returns 0 repos.**
Trending has no official API, so the radar reads the web page. If GitHub redesigns it, the parsing lines in the `trending()` function need updating. Ask Claude Code to fix it, and paste the error.

**Does it predict viral posts?**
No. It removes bad candidates and surfaces ones that are holding attention. Your hook and your edit still do the heavy lifting.

**Can I use it for a non-tech niche?**
It works best where things launch on GitHub or get linked in newsletters: AI, dev tools, design tools, no-code, productivity, open-source anything. For other niches, use the Claude skill's scoring on its own and feed it your own list of ideas.

**Does my data leave my repo?**
No. It only reads public pages, and everything it writes goes into your own repo.
