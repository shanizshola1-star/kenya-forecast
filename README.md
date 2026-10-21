# Kenya 7-day weather forecast

A public dashboard of rainfall and temperature for every county and ward in Kenya.
It updates itself twice a day, at about 7:30 a.m. and 7:30 p.m. Kenya time.

## What's in this folder

- `index.html`: the dashboard page.
- `forecast.js`: the latest forecast numbers. The automatic update replaces this file.
- `scripts/update_forecast.py`: downloads the new forecast and writes `forecast.js`.
- `data/points.json`: the centre point of every ward, used to fetch the forecast.
- `.github/workflows/update-forecast.yml`: the schedule that runs the script twice a day.

## Put it online with GitHub (free)

1. Create a free account at github.com, then click **New repository**. Give it a name such as `kenya-forecast`, set it to **Public**, and click **Create repository**.
2. On the new repository page, click **uploading an existing file**. Drag in everything from this folder, including the `.github` folder, and click **Commit changes**.
   The `.github` folder can be hidden on your computer. If it doesn't upload, create the file on GitHub instead: **Add file › Create new file**, type `.github/workflows/update-forecast.yml` as the name, and paste in the file's contents.
3. Go to **Settings › Pages**. Under **Branch**, pick `main` and `/ (root)`, then click **Save**. After a minute or two, your public link appears at the top of that page, like `https://your-name.github.io/kenya-forecast/`.
4. Go to **Settings › Actions › General**. Under **Workflow permissions**, choose **Read and write permissions** and click **Save**.
5. Go to the **Actions** tab, pick **Update forecast**, and click **Run workflow** to test it. A green tick means it worked. From then on, it runs by itself twice a day.

## If something goes wrong

- If an update fails, the website keeps showing the previous forecast, and GitHub emails the account owner. The **Actions** tab shows what went wrong.
- GitHub can start scheduled runs a few minutes late at busy times.
- GitHub pauses schedules in repositories with no activity for 60 days. The twice-daily updates count as activity, so this shouldn't happen while it's working.

## Data source

The forecast currently comes from Open-Meteo (open-meteo.com), which is free for non-commercial use.
To switch to KMD model output later, only `scripts/update_forecast.py` needs to change. The dashboard and the schedule stay the same.
