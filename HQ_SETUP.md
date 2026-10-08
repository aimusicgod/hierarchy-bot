# Moving the admin panel to its own HQ server

The HQ server is private to you and your admins. It holds the admin panel (uploads, payouts, backups) and the review cards.
Scouts and creators never see it. Each future franchise gets its own HQ.

1. Discord: **+ Add a Server → Create My Own → For me and my friends**. Name it "Hierarchy HQ".
2. Invite the bot to it (Developer Portal → OAuth2 → URL Generator: scopes `bot` + `applications.commands`, permission **Administrator**).
3. Discord Settings → Advanced → turn on **Developer Mode**. Right-click the HQ server icon → **Copy Server ID**.
4. Railway → your service → Variables → add `HQ_GUILD_ID` = that ID. Keep everything else as it is.
5. Upload the new files to GitHub, then **stop the deployment fully** and deploy fresh (Redeploy can reuse the old build).
6. In HQ, type `/setup-hq`. It creates the Admin and Manager roles, a private #admin-panel and #review, and posts the panel.
7. Give yourself the **Admin** role (and your other admins). Test one upload from the new panel.
8. Delete the old admin panel message in the scouts server's review channel.

Nothing in the database moves. Scout cards and approvals now arrive in HQ #review; clicking Approve still gives the role in the scouts server.
