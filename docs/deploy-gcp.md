# Run the Taskman server on Google Cloud

Each push to `staging`, including each merged pull request, deploys the Taskman server to Google Cloud project `nemos-infra` in region `us-central1`. [ADR (Architecture Decision Record) 0001](adrs/0001-deploy-the-server-to-google-cloud.md) gives the design. [`deploy/gcp/nemos-infra.env`](../deploy/gcp/nemos-infra.env) names every resource. Agents and people use the `taskman` command-line interface (CLI) with a Taskman token. They need no Google Cloud credentials.

## Choose a setup path

A project owner sets up `nemos-infra` one time, before the first deployment. Two paths create the same Google Cloud resources:

- **The steps of [Set up the environment once](#set-up-the-environment-once).** A project owner did these steps for `nemos-infra` on Nov 14 and 15, 2026. Each command gives the output of that run. Use this path.
- **The script [`bootstrap.sh`](#set-up-with-bootstrapsh).** It does the Google Cloud steps in one run. Nobody has run it against a real project yet.

## How a deployment runs

The workflow [`deploy-nemos-infra.yml`](../.github/workflows/deploy-nemos-infra.yml):

1. Runs the checks of [`ci.yml`](../.github/workflows/ci.yml): the tests with a PostgreSQL 16 service container, a check of the deployment scripts, and the image build.
2. Signs in to Google Cloud as the service account `taskman-deployer` through Workload Identity Federation. The provider `taskman` admits only the repository `vkroz/taskman` on branch `staging`. GitHub stores no service account key.
3. Builds the image of [`taskman/Dockerfile`](../taskman/Dockerfile) and pushes it to `us-central1-docker.pkg.dev/nemos-infra/taskman/taskman`, tagged with the commit and `staging`.
4. Runs [`deploy/gcp/deploy.sh`](../deploy/gcp/deploy.sh) with the image digest. The Cloud Run job `taskman-db-upgrade` runs `taskman db upgrade`, and the script waits for it. Then the script deploys the Cloud Run service `taskman`.

One deployment runs at a time, in push order. A new push does not cancel a running deployment. A failure at any step stops the deployment. A failed check or build changes nothing in Google Cloud. A failed schema upgrade leaves the new image on the job only: the service keeps its previous image. The server listens only after it connects to the database and finds the current schema. Thus `gcloud run deploy` fails when the new revision cannot start, and the previous revision continues to serve.

After a fix, push to `staging`. To run a deployment again without a new commit, open its run in the Actions tab and select "Re-run all jobs". GitHub shows "Run workflow" only when the workflow file is on the default branch, and `main` has no deployment files.

The service runs `taskman serve` on port 8080, with 1 CPU (central processing unit), 512 MiB (mebibytes) of memory, 0 to 3 instances and a request timeout of 120 seconds. Each instance keeps one database connection. When the connection breaks, for example after a Cloud SQL restart, the next call opens a new connection. A connection that drops with no signal is found within 5 seconds. The first `/readyz` after such a drop can fail one probe. `/readyz` answers 503 only while the database is away. A liveness probe calls `/readyz` every 30 seconds, and after 3 failures Cloud Run replaces the instance.

## Set up the environment once

You need the Owner role on `nemos-infra`, admin access to the GitHub repository `vkroz/taskman`, and a computer with a browser. Do the steps in this order. Paste each command alone, and wait until it stops.

The setup reuses four resources that existed in `nemos-infra` before Nov 14, 2026. The steps only check them:

- the Cloud SQL instance `nemos-pg`
- the Artifact Registry repository `taskman`
- the service account `taskman-deployer`
- the Workload Identity pool `github`

The setup does not use or change the earlier services `taskman-staging` and `taskman-prod`, databases `taskman_staging` and `taskman_prod`, their secrets, or the provider `github-actions`.

### Google Cloud (Cloud Shell)

Cloud Shell is a terminal in the Google Cloud console. It has `gcloud` and `python3`. The role commands change IAM (Identity and Access Management) policies. The checks of the database, user and secret names are necessary only before a first setup: since Nov 14, 2026, these names exist in `nemos-infra`.

1. Open Cloud Shell. Open https://console.cloud.google.com/?project=nemos-infra, sign in, and click the Cloud Shell icon (`>_`) at the top right. If Cloud Shell asks you to authorize `gcloud`, click **Authorize**.

   Result: a terminal opens at the bottom of the page. Its prompt shows `(nemos-infra)`.

2. Select the project for the next commands.

   ```bash
   gcloud config set project nemos-infra
   ```

   Output: `Updated property [core/project].`

3. Check the project number.

   ```bash
   gcloud projects describe nemos-infra --format 'value(projectNumber)'
   ```

   Output: `1037430670088`. If the number is different, stop.

4. Check that no database has the name `taskman`.

   ```bash
   gcloud sql databases list --instance nemos-pg
   ```

   Output: the databases `postgres`, `taskman_staging` and `taskman_prod`. If the list has `taskman`, stop: the name belongs to another application.

5. Check that no user has the name `taskman`.

   ```bash
   gcloud sql users list --instance nemos-pg
   ```

   Output: the users `postgres`, `taskman_prod` and `taskman_staging`. If the list has `taskman`, stop.

6. Check that the two secret names are free.

   ```bash
   gcloud secrets list
   ```

   Output: the secrets `nemos-pg-postgres-password`, `taskman-prod-api-token`, `taskman-prod-db-password`, `taskman-staging-api-token` and `taskman-staging-db-password`. If the list has `taskman-db-password` or `taskman-google-client-secret`, stop.

7. Turn on the APIs (application programming interfaces) that Taskman uses. This takes up to 1 minute.

   ```bash
   gcloud services enable run.googleapis.com sqladmin.googleapis.com secretmanager.googleapis.com artifactregistry.googleapis.com iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com cloudresourcemanager.googleapis.com
   ```

   Output: `Operation "operations/acat.p2-1037430670088-…" finished successfully.`

8. Create the service account `taskman-server`, which runs the schema job and the service.

   ```bash
   gcloud iam service-accounts create taskman-server --display-name "Taskman server"
   ```

   Output: `Created service account [taskman-server].`

9. Check the deployer account. It exists from the earlier deployment, and the setup reuses it.

   ```bash
   gcloud iam service-accounts describe taskman-deployer@nemos-infra.iam.gserviceaccount.com
   ```

   Output: the details of the account, with `email: taskman-deployer@nemos-infra.iam.gserviceaccount.com`. If the output has `NOT_FOUND`, stop, and use [`bootstrap.sh`](#set-up-with-bootstrapsh): it creates missing resources.

10. Check the image repository. It exists, and the setup reuses it.

    ```bash
    gcloud artifacts repositories describe taskman --location us-central1
    ```

    Output: the details of the repository, with `format: DOCKER` and `registryUri: us-central1-docker.pkg.dev/nemos-infra/taskman`. If the output has `NOT_FOUND`, stop, and use [`bootstrap.sh`](#set-up-with-bootstrapsh).

11. Create the secret `taskman-db-password` with a random password. The password does not show.

    ```bash
    python3 -c 'import secrets; print(secrets.token_urlsafe(32), end="")' | gcloud secrets create taskman-db-password --replication-policy automatic --data-file -
    ```

    Output: `Created version [1] of the secret [taskman-db-password].`

12. Create the database `taskman`. This takes up to 1 minute.

    ```bash
    gcloud sql databases create taskman --instance nemos-pg
    ```

    Output: `Created database [taskman].`

13. Create the user `taskman` with the password of the secret. `CLOUDSDK_CORE_DISABLE_FILE_LOGGING=1` keeps the password out of the log files of `gcloud`, as in `bootstrap.sh`. This takes up to 1 minute.

    ```bash
    CLOUDSDK_CORE_DISABLE_FILE_LOGGING=1 gcloud sql users create taskman --instance nemos-pg --password="$(gcloud secrets versions access latest --secret taskman-db-password)"
    ```

    Output: `Created user [taskman].`

    On Nov 14, 2026, the command had no `CLOUDSDK_CORE_DISABLE_FILE_LOGGING=1`. Thus the log files of `gcloud` in Cloud Shell received the password. `gcloud` deletes its log files after 30 days. To delete them now, run `rm -r ~/.config/gcloud/logs` in Cloud Shell.

    Cloud SQL gives the user `taskman` the `cloudsqlsuperuser` role, with `CREATEROLE` and `CREATEDB` on the whole instance ([About PostgreSQL users and roles](https://cloud.google.com/sql/docs/postgres/users)). Other applications share `nemos-pg`.

14. Create the secret for the client secret of browser login. It has no value until [Google sign-in client and secret](#google-sign-in-client-and-secret).

    ```bash
    gcloud secrets create taskman-google-client-secret --replication-policy automatic
    ```

    Output: `Created secret [taskman-google-client-secret].`

15. Let the server read the database password. If the command says that `taskman-server` does not exist, wait 1 minute and run it again. A new service account can take 1 minute to become usable.

    ```bash
    gcloud secrets add-iam-policy-binding taskman-db-password --member serviceAccount:taskman-server@nemos-infra.iam.gserviceaccount.com --role roles/secretmanager.secretAccessor
    ```

    Output: `Updated IAM policy for secret [taskman-db-password].`, then the policy.

16. Let the server read the client secret.

    ```bash
    gcloud secrets add-iam-policy-binding taskman-google-client-secret --member serviceAccount:taskman-server@nemos-infra.iam.gserviceaccount.com --role roles/secretmanager.secretAccessor
    ```

    Output: `Updated IAM policy for secret [taskman-google-client-secret].`

17. Let the server connect to Cloud SQL.

    ```bash
    gcloud projects add-iam-policy-binding nemos-infra --member serviceAccount:taskman-server@nemos-infra.iam.gserviceaccount.com --role roles/cloudsql.client --condition None
    ```

    Output: `Updated IAM policy for project [nemos-infra].`, then the policy of the project.

18. Let the deployer deploy Cloud Run jobs and services. The deployer had this role before Nov 14, 2026, so the command changed nothing then.

    ```bash
    gcloud projects add-iam-policy-binding nemos-infra --member serviceAccount:taskman-deployer@nemos-infra.iam.gserviceaccount.com --role roles/run.developer --condition None
    ```

    Output: `Updated IAM policy for project [nemos-infra].`

19. Let the deployer read the settings of the Cloud SQL instance.

    ```bash
    gcloud projects add-iam-policy-binding nemos-infra --member serviceAccount:taskman-deployer@nemos-infra.iam.gserviceaccount.com --role roles/cloudsql.viewer --condition None
    ```

    Output: `Updated IAM policy for project [nemos-infra].`

20. Let the deployer push images to the repository `taskman`.

    ```bash
    gcloud artifacts repositories add-iam-policy-binding taskman --location us-central1 --member serviceAccount:taskman-deployer@nemos-infra.iam.gserviceaccount.com --role roles/artifactregistry.writer
    ```

    Output: `Updated IAM policy for repository [taskman].`

21. Let the deployer run the job and the service as `taskman-server`.

    ```bash
    gcloud iam service-accounts add-iam-policy-binding taskman-server@nemos-infra.iam.gserviceaccount.com --member serviceAccount:taskman-deployer@nemos-infra.iam.gserviceaccount.com --role roles/iam.serviceAccountUser
    ```

    Output: `Updated IAM policy for serviceAccount [taskman-server@nemos-infra.iam.gserviceaccount.com].`

22. Check the Workload Identity pool `github`. It exists, and the setup reuses it.

    ```bash
    gcloud iam workload-identity-pools describe github --location global
    ```

    Output: the details of the pool, with `name: projects/1037430670088/locations/global/workloadIdentityPools/github` and `state: ACTIVE`. If the output has `NOT_FOUND`, stop, and use [`bootstrap.sh`](#set-up-with-bootstrapsh).

23. Create the provider `taskman`, which admits only the repository `vkroz/taskman` on branch `staging`.

    ```bash
    gcloud iam workload-identity-pools providers create-oidc taskman --location global --workload-identity-pool github --display-name "Taskman deployment" --issuer-uri https://token.actions.githubusercontent.com --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" --attribute-condition "assertion.repository == 'vkroz/taskman' && assertion.ref == 'refs/heads/staging'"
    ```

    Output: `Created workload identity pool provider [taskman].`

24. Let GitHub runs of `vkroz/taskman` sign in as the deployer.

    ```bash
    gcloud iam service-accounts add-iam-policy-binding taskman-deployer@nemos-infra.iam.gserviceaccount.com --role roles/iam.workloadIdentityUser --member "principalSet://iam.googleapis.com/projects/1037430670088/locations/global/workloadIdentityPools/github/attribute.repository/vkroz/taskman"
    ```

    Output: `Updated IAM policy for serviceAccount [taskman-deployer@nemos-infra.iam.gserviceaccount.com].`

25. List the providers of the pool `github`. The deployer trusts every provider of this pool that maps `attribute.repository`, also the earlier provider `github-actions`.

    ```bash
    gcloud iam workload-identity-pools providers list --workload-identity-pool github --location global --format 'table(name.basename(),attributeCondition)'
    ```

    Output:

    ```text
    NAME: github-actions
    ATTRIBUTE_CONDITION: assertion.repository == 'vkroz/taskman' && assertion.repository_owner_id == '938321' && (assertion.ref == 'refs/heads/main' || assertion.ref == 'refs/heads/staging')

    NAME: taskman
    ATTRIBUTE_CONDITION: assertion.repository == 'vkroz/taskman' && assertion.ref == 'refs/heads/staging'
    ```

    Check that each condition admits only branches that you trust. On Nov 14, 2026, the two conditions admitted only `main` and `staging` of `vkroz/taskman`. `taskman-deployer` also keeps its earlier roles; `gcloud iam service-accounts get-iam-policy taskman-deployer@nemos-infra.iam.gserviceaccount.com --project nemos-infra` shows who can use it.

26. Create the public service `taskman` with a placeholder image. The first deployment replaces the placeholder. This takes up to 2 minutes. Do this step only when the service `taskman` does not exist. On an existing service, the placeholder replaces the server until the next deployment. The service exists in `nemos-infra` since Nov 14, 2026.

    ```bash
    gcloud run deploy taskman --region us-central1 --image us-docker.pkg.dev/cloudrun/container/hello --service-account taskman-server@nemos-infra.iam.gserviceaccount.com --no-invoker-iam-check --labels app=taskman
    ```

    Output: `Service [taskman] revision [taskman-00001-…] has been deployed and is serving 100 percent of traffic.`, then `Service URL: https://taskman-1037430670088.us-central1.run.app`. This is [the service address](#the-service-address).

    The service accepts calls without a Google identity: Cloud Run does not check the invoker, and Taskman checks the token of each call. If the command fails with an error that names an organization policy, ask an organization administrator to allow `constraints/run.managed.requireInvokerIam` for the service.

### GitHub environment and variables

The deployment workflow reads two variables of the GitHub environment `nemos-infra`. Both values are fixed for `nemos-infra`. `bootstrap.sh` prints the same values.

1. Create the environment. Open https://github.com/vkroz/taskman/settings/environments and click **New environment**. In **Name**, type `nemos-infra`, then click **Configure environment**.

   Result: GitHub shows the settings of the environment `nemos-infra`.

2. Limit the environment to `staging`. Under **Deployment branches and tags**, select **Selected branches and tags**. Click **Add deployment branch or tag rule**, keep **Ref type: Branch**, type `staging` in **Name pattern**, and click **Add rule**.

   Result: the rule `staging` shows under **Deployment branches and tags**.

3. Add the first variable. Under **Environment variables**, not "Environment secrets", click **Add environment variable**. In **Name**, type `GCP_WORKLOAD_IDENTITY_PROVIDER`. In **Value**, type `projects/1037430670088/locations/global/workloadIdentityPools/github/providers/taskman`. Click **Add variable**.

   Result: the variable shows under **Environment variables**.

4. Add the second variable the same way. In **Name**, type `GCP_DEPLOYER_SERVICE_ACCOUNT`. In **Value**, type `taskman-deployer@nemos-infra.iam.gserviceaccount.com`.

   Result: the two variables show under **Environment variables**.

5. Start the first deployment. Since Nov 15, 2026, `deploy/gcp/nemos-infra.env` sets `GOOGLE_CLIENT_ID`. A deployment with a client ID fails while the secret `taskman-google-client-secret` has no value. Thus first create the client and store its secret, with steps 1 to 4 of [Google sign-in client and secret](#google-sign-in-client-and-secret). On Nov 14, 2026, `GOOGLE_CLIENT_ID` was empty, and the first deployment ran before these steps.

   Merge a change to `staging`. If a run of **Deploy to nemos-infra** stopped at "Check the one-time setup" before the setup, open that run and select "Re-run all jobs". On Nov 14, 2026, the run of an earlier merge started again this way.

   Result: every job of the run passes. The schema job creates the tables, and the service replaces the placeholder.

6. Check the server. Open `https://taskman-1037430670088.us-central1.run.app/readyz` in a browser. [Check deployments](#check-deployments) tells why you use only `/readyz`.

   Result: `{"ok":true}`.

### Google sign-in client and secret

With browser login, each person signs in with a Google account and receives a Taskman token. You create the OAuth (Open Authorization) client in Google Auth Platform, in the Google Cloud console of `nemos-infra`. You need the Owner role or OAuth Config Editor (`roles/oauthconfig.editor`).

1. Configure Google Auth Platform. Open https://console.cloud.google.com/auth/overview?project=nemos-infra and click **Get started**. Complete the four pages:
   - **App Information:** in **App name**, type `Taskman`. In **User support email**, select your address.
   - **Audience:** select **Internal** when each person signs in with an account of the Google Workspace organization of the project, as for `nemos-infra`. For a personal Gmail account, or another account outside the organization, select **External**, and later add each person under **Audience**, **Test users**.
   - **Contact Information:** type your email address.
   - **Finish:** agree to the Google API Services User Data Policy, click **Continue**, then **Create**.

   Result: `OAuth configuration created!` If the page shows an overview and no **Get started**, the configuration exists: continue with the client.

2. Create the client. Click **Clients**, then **Create client**. In **Application type**, select **Web application**. In **Name**, type `Taskman server`. Under **Authorized redirect URIs** (Uniform Resource Identifiers), click **Add URI** and type `https://taskman-1037430670088.us-central1.run.app/auth/callback`. This is `PUBLIC_URL/auth/callback`: see [The service address](#the-service-address). Click **Create**.

   Result: `OAuth client created`, and a window shows the client ID and the client secret. Keep the window open for the next step. Google can take 5 minutes to a few hours to apply a new or changed redirect URI.

3. Store the client secret, as [Store the client secret](#store-the-client-secret) shows. Store only the value that starts with `GOCSPX-`.

   Output: `Created version [<number>] of the secret [taskman-google-client-secret].` The number is the next version number of the secret. On Nov 15, 2026, it was 2.

4. Check the stored value. The command shows the length and the start of the value, not the value.

   ```bash
   gcloud secrets versions access latest --secret taskman-google-client-secret --project nemos-infra | python3 -c 'import sys; s=sys.stdin.read(); print("length", len(s), "| starts with GOCSPX-:", s.startswith("GOCSPX-"), "| spaces or newlines at ends:", s != s.strip())'
   ```

   Output: `length 35 | starts with GOCSPX-: True | spaces or newlines at ends: False`.

5. Turn on browser login. In `deploy/gcp/nemos-infra.env`, set `GOOGLE_CLIENT_ID` to the client ID, and set [who may sign in](#who-may-sign-in). `PUBLIC_URL` must hold the service address. Merge the change to `staging`. On Nov 15, 2026, the change set `GOOGLE_CLIENT_ID=1037430670088-pl9k3458ngrm78nlpr6lseqrmc9a436m.apps.googleusercontent.com` and `ALLOWED_DOMAINS=reservoirco.io`.

   When the file already has these values, there is no change to merge. If no deployment started after you stored the client secret, re-run the last deployment, as [Store the client secret](#store-the-client-secret) shows.

   Result: the run of **Deploy to nemos-infra** passes, and browser login is on.

The first login on Nov 15, 2026 needed two fixes. These steps prevent both errors, so do not do the fixes again:

- The client also lists the redirect URI of the second address, `https://taskman-<hash>-uc.a.run.app/auth/callback`. At that time, the server sent this address to Google. Now it sends `PUBLIC_URL`, so you can remove this URI from the client.
- Version 1 of `taskman-google-client-secret` holds the downloaded client file. Version 2 holds only the client secret, and a new deployment read it. If version 1 is enabled, disable it, as [Store the client secret](#store-the-client-secret) shows.

### First login from a computer

Each person does these steps on a computer with a browser. They need [uv](https://docs.astral.sh/uv/). The outputs are from a Mac on Nov 15, 2026.

1. Check that uv is installed.

   ```bash
   uv --version
   ```

   Output: a version, for example `uv 0.13.7 (Homebrew 2026-11-03 aarch64-apple-darwin)`. If the shell cannot find the command, install uv as its [installation page](https://docs.astral.sh/uv/getting-started/installation/) shows.

2. Install the CLI from `staging`. The CLI on `main` has no `auth` command.

   ```bash
   uv tool install --force 'git+https://github.com/vkroz/taskman.git@staging#subdirectory=taskman'
   ```

   Output: the installed packages, then `Installed 2 executables: taskman, tm`.

3. Log in. The command opens the Google sign-in page in the browser. Select your `reservoirco.io` account. When the page shows "You are logged in to Taskman. You can close this tab.", close the tab.

   ```bash
   taskman auth login --server https://taskman-1037430670088.us-central1.run.app
   ```

   Output: `Open this address in a browser on this computer to log in:` and the address, then `Logged in to https://taskman-1037430670088.us-central1.run.app as <you>@reservoirco.io.` If the login fails, see [Fix a failed login](#fix-a-failed-login).

4. Check the login.

   ```bash
   taskman auth status
   ```

   Output: `Logged in to https://taskman-1037430670088.us-central1.run.app as <you>@reservoirco.io. Token from /Users/<you>/.config/taskman/credentials.json.`

5. Make one call to the server.

   ```bash
   taskman list
   ```

   Output: `(no items)` while the database has no items, else the items.

### Set up with bootstrap.sh

[`deploy/gcp/bootstrap.sh`](../deploy/gcp/bootstrap.sh) does the [Google Cloud steps](#google-cloud-cloud-shell) in one run. You need `gcloud`, signed in as an Owner, and `python3`, for example in Cloud Shell. Run the script from the root of a checkout of `staging`:

```bash
deploy/gcp/bootstrap.sh deploy/gcp/nemos-infra.env
```

A repeated run creates only what is missing and never replaces a password. When the secret `taskman-db-password` has no value but the database `taskman` or the user `taskman` exists on `nemos-pg`, the script stops. At that point, it has turned on the APIs and set up no resource. These names belong to another application: choose other `DB_NAME` and `DB_USER` values in the environment file, and run the script again. If an organization policy blocks the public service, the script prints what to ask of an organization administrator.

The script lists the providers of the pool `github`: check their conditions, as the provider list of the Google Cloud steps shows. At the end, it prints the two GitHub variables, the service address and the redirect URI. Then continue with [GitHub environment and variables](#github-environment-and-variables).

The script differs from the Google Cloud steps in these points:

- It gives `--project` to each command, and `--quiet` to the deployment of the placeholder. The steps select the project one time.
- It stops at a taken database or user name only when the password secret has no value. Thus a repeated run continues.
- It creates the four reused resources when they are missing. A new instance `nemos-pg` has the tier `SQL_TIER`.
- It creates the password secret and its first version with two commands. When the user `taskman` exists, it sets the password of the user from the secret.
- It gives `taskman-server` the display name `Taskman taskman-server`.
- It reads the project number for the deployer binding. The steps contain `1037430670088`.
- When the service `taskman` exists, it does not deploy the placeholder. It only makes the service public.

## Browser login

### The service address

The service address is `https://taskman-1037430670088.us-central1.run.app`. It is `PUBLIC_URL` in `deploy/gcp/nemos-infra.env`. `gcloud run deploy` and `bootstrap.sh` print it. People, agents and Google use only this address. Cloud Run also gives the service a second address, `https://taskman-<hash>-uc.a.run.app`. Do not use it: Google accepts only a registered redirect URI, and the CLI keeps each login for one exact address.

### Store the client secret

Put only the value of the client secret into the secret `taskman-google-client-secret`. The value starts with `GOCSPX-`. Do not put the downloaded file `client_secret_*.json` into it: Google then refuses each sign-in with `invalid_client`. Do this before you set the client ID: a deployment with a client ID fails while this secret has no value.

In Cloud Shell, `read` waits for you to paste the client secret and press Enter. The terminal does not show it. Then the second command stores the value, and the third command removes it from the shell.

```bash
read -rs CLIENT_SECRET
```

```bash
printf %s "$CLIENT_SECRET" | gcloud secrets versions add taskman-google-client-secret --project nemos-infra --data-file -
```

```bash
unset CLIENT_SECRET
```

If you downloaded the client as a JSON file, this command takes the client secret from the file. It does not show the value. Replace `<downloaded file>` with the path of the file:

```bash
python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["web"]["client_secret"], end="")' <downloaded file> | gcloud secrets versions add taskman-google-client-secret --project nemos-infra --data-file -
```

A server instance reads the secret only when it starts. When browser login is on, a new version thus needs a new deployment. Open the last deployment run in the Actions tab, and select "Re-run all jobs". Then find the old version, and disable it:

```bash
gcloud secrets versions list taskman-google-client-secret --project nemos-infra
```

```bash
gcloud secrets versions disable <old version> --secret taskman-google-client-secret --project nemos-infra
```

### Who may sign in

Set `ALLOWED_DOMAINS`, `ALLOWED_EMAILS`, or both, in `deploy/gcp/nemos-infra.env`, as comma-separated lists without spaces. `ALLOWED_DOMAINS` admits the members of a Google Workspace organization. A personal Gmail account has no Workspace domain: put its address in `ALLOWED_EMAILS`. To change who may sign in, change the lists and merge to `staging`. `deploy.sh` stops before it changes anything when `GOOGLE_CLIENT_ID` is set and `PUBLIC_URL` is empty, or both lists are empty. To turn browser login off, empty `GOOGLE_CLIENT_ID` and merge to `staging`.

When browser login is on, anyone can open `/auth/cli` and the workspace page on the public service. Each request adds a row to the `logins` table under the database write lock, and Taskman does not limit the rate of these requests. Each new request deletes a row after 1 day when Google signed in a person who may use the server, and after 1 hour in all other cases, also when the sign-in returned with an error.

### Fix a failed login

When a login fails, the Taskman page or the Google page names an OAuth error code:

- `invalid_client`: the client ID or the client secret is wrong. Compare `GOOGLE_CLIENT_ID` with the client in Google Auth Platform. Add the correct client secret as a new version, then re-run the last deployment.
- `invalid_grant`: the sign-in code expired, or Taskman used it before. Start the login again.
- `redirect_uri_mismatch`: `PUBLIC_URL/auth/callback` is not an authorized redirect URI of the client. Add it, and wait until Google applies the change.

## Use the server

Each person logs in from a computer with a browser, as [First login from a computer](#first-login-from-a-computer) shows. On a machine without a browser, open Settings > Tokens at `https://taskman-1037430670088.us-central1.run.app/settings/tokens` on any computer and sign in. Create a token with a name and an expiry. The page shows the token one time. On the machine, run `taskman auth login --server https://taskman-1037430670088.us-central1.run.app --with-token` and paste the token. To connect an agent cloud environment, follow [Connect an agent cloud environment](../README.md#connect-an-agent-cloud-environment) in the README. Without browser login, an operator creates the token, as "Run operator commands" shows.

Each person lists and revokes their own tokens with `taskman token list` and `taskman token revoke ID` against the server, or on Settings > Tokens after the sign-in. [Manage your tokens](../README.md#manage-your-tokens) in the README gives the steps.

### Open the workspace page

With browser login on, open `https://taskman-1037430670088.us-central1.run.app/` in a browser, or a link from **Copy link**, such as `https://taskman-1037430670088.us-central1.run.app/items/TASKMAN-55`. The server sends you to Google. Sign in with an account that `ALLOWED_DOMAINS` or `ALLOWED_EMAILS` admits. Google sends you back to the address that you opened. Another account gets a page that says that it may not use the server.

You are the author of each change that you make on the page. The session lasts 7 days from the sign-in, and use does not extend it. When it ends, the page signs you in again and returns to the same address. To end the session on the server, click your name at the top of the page, then **Sign out**. Each sign-in adds a row to the `logins` table, as a CLI login does, and a row to the `sessions` table. A new sign-in deletes the sessions that ended or expired. Without browser login, the page at `/` says that browser login is not set up.

## Run operator commands

`taskman token list --database`, `taskman token revoke --database` and `taskman token create --database` open the database directly and reach the tokens of every person. Without `--database`, `token list` and `token revoke` use the server of your login and reach only your own tokens. Run them on your computer through the [Cloud SQL Auth Proxy version 2](https://github.com/GoogleCloudPlatform/cloud-sql-proxy), with [uv](https://docs.astral.sh/uv/) and a checkout of this repository. You need the roles Cloud SQL Client and Secret Manager Secret Accessor; the Owner role has them. The proxy uses your Application Default Credentials: run `gcloud auth application-default login` one time.

```bash
cloud-sql-proxy --port 15432 nemos-infra:us-central1:nemos-pg &
export TASKMAN_BACKEND=postgres
export TASKMAN_DATABASE_URL=postgresql://taskman@127.0.0.1:15432/taskman
export TASKMAN_DB_PASSWORD="$(gcloud secrets versions access latest --secret taskman-db-password --project nemos-infra)"
cd taskman
uv run --extra postgres taskman token list --database
```

In the same terminal, `uv run --extra postgres taskman token revoke ID --database` stops a token of any person. `uv run --extra postgres taskman token create --person "ada@example.com" --database` creates a token in an emergency. When you are done:

```bash
cd ..
unset TASKMAN_BACKEND TASKMAN_DATABASE_URL TASKMAN_DB_PASSWORD
kill %1
```

- `uv run` uses the Taskman code of this checkout. Check out the commit of the deployed image: Taskman refuses a database with a different schema version.
- Do not run `token create` as a Cloud Run job. Cloud Logging keeps the output of a job, and the token would stay there.

## Check deployments

Each deployment is a run of the workflow **Deploy to nemos-infra** in the [Actions tab](https://github.com/vkroz/taskman/actions/workflows/deploy-nemos-infra.yml). The summary of a successful run names the commit and the image digest. A failed run names the failed step: the checks, the check of the one-time setup, the sign-in, the build, or the deploy. The log of the schema upgrade job is in Cloud Logging:

```bash
gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="taskman-db-upgrade"' --project nemos-infra --limit 50 --format 'value(textPayload)'
```

The [Cloud Run page](https://console.cloud.google.com/run?project=nemos-infra) shows the image of the job and of the service. `curl -fsS https://taskman-1037430670088.us-central1.run.app/readyz` prints `{"ok":true}` when the server can query the database. `/readyz` needs no token. Do not use `/healthz` on Cloud Run: Google's front end reserves some paths that end in `z`, and it answers `/healthz` with its own 404 page. The request does not reach Taskman.

## Deploy by hand or roll back

`deploy.sh` deploys one image with your own `gcloud` sign-in. You need the roles of the deployer: Cloud Run Developer, Cloud SQL Viewer, and Service Account User on `taskman-server`. The Owner role has them. The script takes every setting from the environment file of your checkout, so deploy from a current checkout of `staging`.

To roll back, deploy an earlier image by its digest. The summary of each workflow run names the image as a full `...@sha256:...` reference. The column `IMAGE_DIGEST` of the revisions shows the same reference:

```bash
gcloud run revisions list --service taskman --project nemos-infra --region us-central1 --format 'table(metadata.name,status.imageDigest,metadata.creationTimestamp)'
deploy/gcp/deploy.sh deploy/gcp/nemos-infra.env <image reference>
```

Schema upgrades go forward only. With an image that has an older schema version than the database, the upgrade job fails, and the service keeps its image. Roll back only to an image with the current schema version. The next push to `staging` deploys its own image again.

## Another project or cloud

Another Google Cloud project needs its own environment file. Copy `deploy/gcp/nemos-infra.env` and change the names. Empty `PUBLIC_URL` in the copy: it holds the address of the `nemos-infra` service. When `PUBLIC_URL` has a value, `bootstrap.sh` prints that value. Run `bootstrap.sh` with the new file. Then set `PUBLIC_URL` to the service address that it prints. Add a workflow like `deploy-nemos-infra.yml` that reads the file.

The same image runs on another platform that gives:

- One container from the image, with environment variables.
- Secrets as files or as environment variables. Taskman reads both.
- PostgreSQL 16, through a connection URL (Uniform Resource Locator) or a unix socket.
- A one-time task that runs `taskman db upgrade` before a new image serves traffic.
- HTTPS (Hypertext Transfer Protocol Secure) in front of the server.

The table shows the equivalent services on AWS (Amazon Web Services). The Taskman maintainers have not tested the AWS path.

| Google Cloud | AWS |
| --- | --- |
| Artifact Registry | Amazon ECR (Elastic Container Registry) |
| Cloud Run service | Amazon ECS (Elastic Container Service) service on AWS Fargate |
| HTTPS address of the Cloud Run service | Application Load Balancer with an AWS Certificate Manager (ACM) certificate |
| Cloud SQL for PostgreSQL | Amazon RDS (Relational Database Service) for PostgreSQL |
| Secret Manager | AWS Secrets Manager |
| Cloud Run job for `db upgrade` | ECS task that `aws ecs run-task` starts |

On AWS, set `TASKMAN_DATABASE_URL` without the password, for example `postgresql://taskman@<RDS endpoint>:5432/taskman`. ECS gives a secret to the container as an environment variable, so put the password in `TASKMAN_DB_PASSWORD`.
