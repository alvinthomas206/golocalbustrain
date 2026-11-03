# GoLocal

GoLocal is a student project prototype for discovering Indian train journeys and Kerala private bus connections.

The launch page lets passengers choose the train planner or the Kerala bus planner.

## Run locally

1. Install Python 3.9 or newer.
2. In this folder, install the Python dependency:

   ```powershell
   python -m pip install -r requirements.txt
   ```

3. Start the app:

   ```powershell
   python dev_server.py
   ```

4. Open [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Deploy to Vercel

1. Push this project to a GitHub repository.
2. Import the repository in Vercel and deploy it with the **Other** framework preset. The project is configured to serve the static app and run the Python endpoints in `api/`.
3. No API keys or environment variables are required for this prototype.

The app includes a PWA manifest, install prompt, icons, and an offline-cached app shell. Train searches and live railway information still require an internet connection. PWA installation requires HTTPS; Vercel deployments provide HTTPS.

The backend calls the `ntes-client` library for trains between stations, schedules, and train running status. The station picker uses `stations.json`, a directory of more than 8,900 Indian railway stations, and can search by station name, code, or state.

Journey results have separate direct and connecting tabs. Connecting plans without a chosen mid-stop look for a single transfer through nearby major junctions. With a mid-stop selected, GoLocal can add a nearby feeder station before that stop, then build onward legs to the destination. It checks a minimum 45-minute connection and the selected date's run days. These are timetable-based suggestions; confirm the itinerary with NTES before travel.

The station directory is from [prasenjit-27/Indian-Railway-Data](https://github.com/prasenjit-27/Indian-Railway-Data), which publishes it under the MIT License. It is a community dataset, not an official Indian Railways station feed; verify codes with official railway sources where accuracy is critical.

The Kerala private bus planner bundles the district timetable JSON files and a 4,277-stop directory from [amith-vp/Kerala-Private-Bus-Timing](https://github.com/amith-vp/Kerala-Private-Bus-Timing), so bus searches do not need to download schedules from GitHub at runtime. It caches parsed timetables in memory for six hours. Vercel includes the `bus-data/` files in its bus API function bundle. It can suggest multi-leg trips with a user-selected transfer limit or no transfer limit. This community-converted dataset covers Kerala private buses only, may be incomplete or out of date, and does not provide live tracking; confirm schedules with operators. The app links back to the source repository and does not present the data as official.

The selected NTES client is unofficial and reverse-engineered. Its repository describes it as intended for educational and personal use, with no uptime or accuracy guarantee. Review the upstream terms and obtain the necessary authorization before public or commercial use. It does not provide ordinary fare lookup or seat availability, so GoLocal does not display invented prices or booking availability.
