# GoLocal bus data on Firebase

The admin portal at `/admin.html` stores bus records in the `busVehicles` Firestore collection. Each document represents one bus and includes its name, vehicle number, district, route, ordered trips, stops, arrival/departure times, and searchable stop keys. The bundled public timetable imports 8,483 bus records containing 93,437 trips. A bus record is kept as one document so its trips can be edited together.

## One-time Firebase setup

1. Open Firebase project `agile-tracker-472015-p8` and create a Cloud Firestore database.
2. In **Authentication → Sign-in method**, enable **Email/Password**.
3. In **Authentication → Users**, create the administrator account `admin@golocal.com`, set its password, and mark its email as verified in the user record. The portal has no public sign-up.
4. From this project folder, sign in to Firebase CLI and publish the included rules:

   ```powershell
   npm install -g firebase-tools
   firebase login
   firebase deploy --only firestore:rules
   ```

   The rules make timetable reads public for journey search and allow changes only from the verified `admin@golocal.com` account.

5. In the GoLocal Vercel project, add `FIREBASE_SERVICE_ACCOUNT_JSON` as an environment variable. Create a Google Cloud service account for this project with the **Cloud Datastore Viewer** role, download its JSON key, and paste the complete JSON file contents as the variable value. Do not paste the filename or local path, Firebase web config, or API key; do not wrap the full JSON object in quotes. Keep the key in Vercel only; never commit it or paste it into the public web configuration. The key gives the journey API read access so it can use administrator-edited Firebase data. The admin portal writes through Firebase Authentication and the Firestore rules.
6. Redeploy Vercel after saving the environment variable.

## Import and manage bus data

1. Open `/admin.html` on the deployed app and sign in as `admin@golocal.com`.
2. Choose **Import source timetable**. The importer reads the bundled district JSON files and uploads records in batches. It skips records already imported, so it can resume after a partial upload without replacing later admin edits.
3. Select any bus record or choose **Add bus**. Edit the bus name, vehicle number, district, trip list, stop order, and arrival/departure times, then save.

Every save, delete, and completed import increments `busMetadata/state.revision`. The bus API checks this small revision document and refreshes its in-memory timetable when it changes, so admin updates are reflected in searches without waiting for the cache to expire.

The Firebase web API key in `firebase-config.js` identifies the project; it is not an admin credential. Firestore security rules and the read-only service account protect the data. Review Firebase usage after the initial import; Firestore applies its published free quotas and document limits.

## Web API key and access controls

The Firebase web API key in `firebase-config.js` is public by design and is sent to browsers. Moving it to a Vercel environment variable would not make it secret. It identifies the Firebase project; authorization is enforced by Firebase Authentication, Firestore Security Rules, and optionally Firebase App Check. Google documents Firebase API keys as safe to include in checked-in app configuration when used only for Firebase services: <https://firebase.google.com/docs/projects/api-keys>.

In Google Cloud Console, open **APIs & Services → Credentials** and review the web key's API restrictions. Keep it limited to the Firebase APIs this app uses (Authentication and Cloud Firestore); do not allow unrelated APIs such as Generative Language. If applying website restrictions, allow the production GoLocal domain and the development origins you actually use. Test sign-in and bus search after any restriction change. Do not delete or rotate the key unless `firebase-config.js` is updated to the replacement key.

Keep `FIREBASE_SERVICE_ACCOUNT_JSON` and any downloaded service-account JSON private. Never commit them. The service account is separate from the public web API key and should have only the server permissions the app needs.

## Admin list behavior

The admin portal reads bus records from `busVehicles`, shows the Firestore total, and pages through records in batches of 100. Search results can also be paged. Adding or editing a bus writes directly to Firestore; each change updates `busMetadata/state.revision`, which refreshes open admin lists and the journey API's timetable cache.
