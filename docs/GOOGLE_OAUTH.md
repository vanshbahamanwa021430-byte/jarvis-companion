# Google OAuth setup for Jarvis Companion v2.2

Do **not** reuse a client from another project (including Prvlar). Create a dedicated Desktop client for this desk. This repo does not ship a client id or a client secret.

The phone never talks to Google directly for Gmail, Calendar, Drive, Contacts or Tasks. You sign in on the computer; the desk holds the tokens and runs approved API calls. YouTube and Maps are opened on the phone with ordinary intents and do not need OAuth.

Tokens are stored as a mode-0600 file under `.local-demo/google-tokens.json`. This build does not encrypt that file at rest (the server stays standard-library only). Protect the computer.

## Create the OAuth client

1. Open the Google Cloud Console and create or pick a project that is only for this personal desk.
2. Open **APIs & Services → OAuth consent screen**. Choose **External** (or Internal if you have a Workspace). Fill in the app name “Jarvis Companion (personal)”, your email, and save. This is a personal / testing app: add your own Google account as a test user.
3. Enable only the APIs you actually want:
   - Gmail API
   - Google Calendar API
   - Google Drive API
   - People API (Contacts)
   - Google Tasks API
4. Open **APIs & Services → Credentials → Create credentials → OAuth client ID**.
5. Application type: **Desktop app**. Name it “Jarvis Companion desk”.
6. Copy the **Client ID** only. You do not need a client secret: this desk uses PKCE. Do not put a client secret in `config.json`.
7. Under the consent screen, add the least-privilege scopes you will actually connect. The desk requests them incrementally:

   | Service  | Read scope | Write / send scope |
   |----------|------------|--------------------|
   | Gmail    | `gmail.readonly` | `gmail.send` (send only, not full mail) |
   | Calendar | `calendar.readonly` | `calendar.events` |
   | Drive    | `drive.readonly` | `drive.file` (files this app creates) |
   | Contacts | `contacts.readonly` | — |
   | Tasks    | `tasks.readonly` | `tasks` |

   Do not add `https://mail.google.com/` or `drive` (full Drive).

8. On the desk, open the **Connect** tab, paste the client id, and click **Save client id**. You can also set `google_client_id` in `config.json` or pass `--google-client-id`.
9. Click **Connect read** (or **Connect send** / **Connect write**) for a service. Complete the Google sign-in in the computer browser. The redirect is `http://127.0.0.1:<desk-port>/api/google/callback`. Finish this step on the computer, not inside the Android WebView (Google does not support WebView OAuth).
10. After a service is connected, a voice command or a Connect-tab action still only *proposes* a request. You allow or deny it once. Send and write always need that approval.

## Revoke access

Use **Disconnect** on the Connect tab, and also revoke the app in your Google Account’s security settings if you are done with it.
