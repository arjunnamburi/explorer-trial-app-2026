import streamlit as st
import datetime
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from supabase import create_client, Client
import pytz
import hashlib
import random

# --- 1. SETUP & CONFIG ---
st.set_page_config(page_title="Explorer: Master Portal", layout="wide")
IST = pytz.timezone('Asia/Kolkata')
geolocator = Nominatim(user_agent="explorer_master_final_v18")

# --- MOBILE UI COMPACT CSS ---
st.markdown("""
    <style>
        html, body, [class*="st-"] { font-size: 14px !important; }
        .stButton button { padding-top: 0.1rem !important; padding-bottom: 0.1rem !important; min-height: 1.8rem !important; }
        [data-testid="column"] { min-width: 0px !important; }
        div[data-testid="stVerticalBlock"] > div { padding: 0 !important; gap: 0.2rem !important; }
    </style>
""", unsafe_allow_html=True)

@st.cache_resource
def init_supabase() -> Client:
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

supabase = init_supabase()

# --- 2. INITIALIZE SESSION STATE ---
if "authenticated" not in st.session_state: st.session_state.authenticated = False
if "user_id" not in st.session_state: st.session_state.user_id = None
if "user_symbol" not in st.session_state: st.session_state.user_symbol = "❓"
if "view" not in st.session_state: st.session_state.view = "Live Dashboard"
if "selected_team" not in st.session_state: st.session_state.selected_team = None
if "itinerary" not in st.session_state: st.session_state.itinerary = []
if "action_mode" not in st.session_state: st.session_state.action_mode = None
if "edit_index" not in st.session_state: st.session_state.edit_index = None
if "insert_index" not in st.session_state: st.session_state.insert_index = None

# --- 3. HELPER FUNCTIONS ---
def hash_password(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def get_team_symbol(team_data):
    return team_data.get('symbol', "📍")

def validate_location(location_name):
    if location_name == "-": return True, location_name, None, None
    try:
        location = geolocator.geocode(location_name, country_codes="in")
        if location and location.latitude and location.longitude:
            return True, location_name.title(), location.latitude, location.longitude 
        return False, None, None, None
    except (GeocoderTimedOut, Exception):
        return False, None, None, None

def is_valid_time_range(d_in, t_in, d_out, t_out):
    start = datetime.datetime.combine(d_in, t_in)
    end = datetime.datetime.combine(d_out, t_out)
    return end > start

def resolve_timeline_overlaps():
    """Ensures steps are perfectly sequential. Step N Start = Step N-1 End."""
    if len(st.session_state.itinerary) < 2: return
    
    idx = st.session_state.edit_index if st.session_state.edit_index is not None else 0
    
    # Push changes FORWARD
    for i in range(idx + 1, len(st.session_state.itinerary)):
        prev = st.session_state.itinerary[i-1]
        p_end = datetime.datetime.combine(prev['Day Out'], prev['Time Out'])
        st.session_state.itinerary[i]['Day In'] = p_end.date()
        st.session_state.itinerary[i]['Time In'] = p_end.time()
        
        # Ensure duration is at least 1 min
        curr_end = datetime.datetime.combine(st.session_state.itinerary[i]['Day Out'], st.session_state.itinerary[i]['Time Out'])
        if curr_end <= p_end:
            new_end = p_end + datetime.timedelta(minutes=30)
            st.session_state.itinerary[i]['Day Out'] = new_end.date()
            st.session_state.itinerary[i]['Time Out'] = new_end.time()

    # Push changes BACKWARD
    for i in range(idx - 1, -1, -1):
        nxt = st.session_state.itinerary[i+1]
        n_start = datetime.datetime.combine(nxt['Day In'], nxt['Time In'])
        st.session_state.itinerary[i]['Day Out'] = n_start.date()
        st.session_state.itinerary[i]['Time Out'] = n_start.time()
        
        curr_start = datetime.datetime.combine(st.session_state.itinerary[i]['Day In'], st.session_state.itinerary[i]['Time In'])
        if curr_start >= n_start:
            new_start = n_start - datetime.timedelta(minutes=30)
            st.session_state.itinerary[i]['Day In'] = new_start.date()
            st.session_state.itinerary[i]['Time In'] = new_start.time()

def serialize_itinerary(itinerary_list):
    serialized = []
    for item in itinerary_list:
        new_item = item.copy()
        new_item['Day In'], new_item['Day Out'] = item['Day In'].isoformat(), item['Day Out'].isoformat()
        new_item['Time In'], new_item['Time Out'] = item['Time In'].strftime('%H:%M:%S'), item['Time Out'].strftime('%H:%M:%S')
        serialized.append(new_item)
    return serialized

def deserialize_itinerary(json_data):
    if not json_data: return []
    deserialized = []
    for item in json_data:
        new_item = item.copy()
        new_item['Day In'], new_item['Day Out'] = datetime.date.fromisoformat(item['Day In']), datetime.date.fromisoformat(item['Day Out'])
        new_item['Time In'], new_item['Time Out'] = datetime.datetime.strptime(item['Time In'], '%H:%M:%S').time(), datetime.datetime.strptime(item['Time Out'], '%H:%M:%S').time()
        deserialized.append(new_item)
    return deserialized

def get_current_status(itinerary):
    now = datetime.datetime.now(IST).replace(tzinfo=None)
    if not itinerary: return None, "No Itinerary Uploaded Yet", None
    for item in itinerary:
        start, end = datetime.datetime.combine(item['Day In'], item['Time In']), datetime.datetime.combine(item['Day Out'], item['Time Out'])
        if start <= now <= end:
            if item['Type'] == "Place":
                return (item['lat'], item['lon']), f"Current Status📍: {item['Location']} until {end.strftime('%H:%M, %d %b')}", None
            mid_lat, mid_lon = (item['lat'] + item.get('dest_lat', item['lat'])) / 2, (item['lon'] + item.get('dest_lon', item['lon'])) / 2
            line = [[item['lat'], item['lon']], [item['dest_lat'], item['dest_lon']]]
            status = f"Current Status🚆: {item['Location']} -> {item['Destination']} from {start.strftime('%d %b, %H:%M')} to {end.strftime('%d %b, %H:%M')}"
            return (mid_lat, mid_lon), status, line
    if now < datetime.datetime.combine(itinerary[0]['Day In'], itinerary[0]['Time In']): return None, "Current Status📍: Journey not started", None
    if now > datetime.datetime.combine(itinerary[-1]['Day Out'], itinerary[-1]['Time Out']): return None, "Current Status📍: Journey completed", None
    return None, "Current Status📍: In between steps", None

def get_jittered_coords(coords, seen_coords, team_name):
    if not coords: return None
    lat, lon = coords
    random.seed(team_name)
    while (lat, lon) in seen_coords:
        lat += random.uniform(-0.00015, 0.00015)
        lon += random.uniform(-0.00015, 0.00015)
    seen_coords.add((lat, lon))
    return (lat, lon)

def find_overlaps(current_team_id, all_teams_data):
    my_team_data = next((t for t in all_teams_data if t['team_name'] == current_team_id), None)
    if not my_team_data: return []
    my_itin = deserialize_itinerary(my_team_data['itinerary_data'])
    overlaps = []
    for other_team in all_teams_data:
        if other_team['team_name'] == current_team_id: continue
        other_itin = deserialize_itinerary(other_team['itinerary_data'])
        for my_step in my_itin:
            if my_step.get('Type') != "Place": continue
            for their_step in other_itin:
                if their_step.get('Type') != "Place": continue
                if my_step['Location'].strip().lower() == their_step['Location'].strip().lower():
                    my_s, my_e = datetime.datetime.combine(my_step['Day In'], my_step['Time In']), datetime.datetime.combine(my_step['Day Out'], my_step['Time Out'])
                    their_s, their_e = datetime.datetime.combine(their_step['Day In'], their_step['Time In']), datetime.datetime.combine(their_step['Day Out'], their_step['Time Out'])
                    o_start, o_end = max(my_s, their_s), min(my_e, their_e)
                    if o_start < o_end: overlaps.append({'team': other_team['team_name'], 'location': my_step['Location'], 'start': o_start, 'end': o_end})
    return overlaps

# --- 4. AUTH ---
if not st.session_state.authenticated:
    st.title("🛡️ Explorer 2026 App : Authentication")
    tab1, tab2 = st.tabs(["Login", "Register"])
    with tab1:
        with st.form("login_form"):
            login_id, login_pass = st.text_input("Team ID"), st.text_input("Password", type="password")
            if st.form_submit_button("Enter"):
                res = supabase.table("team_itineraries").select("*").eq("team_name", login_id).execute()
                if res.data and res.data[0]['password'] == hash_password(login_pass):
                    st.session_state.authenticated, st.session_state.user_id = True, login_id
                    st.session_state.user_symbol = get_team_symbol(res.data[0])
                    st.session_state.view = "Live Dashboard"; st.rerun()
                else: st.error("Invalid ID or Password")
    with tab2:
        with st.container(border=True):
            st.markdown("""**Record your Team ID(Team Name) and Password somewhere.** If it is forgotten, it cannot be recovered. You need to login into the website everytime you open it, so a simple password is advised. """)
        existing_res = supabase.table("team_itineraries").select("symbol").execute()
        existing_symbols = [row.get("symbol") for row in existing_res.data if row.get("symbol")] if existing_res.data else []
        all_syms = ["💎", "🏆", "🎖", "🔱", "🛡", "🔮", "⌚️", "🗿", "🎲", "🎱", "🎩", "🏮", "🎁", "🔭", "🗝", "🚀", "🛸", "🏎", "🏍", "🚁", "🚢", "🚜", "🚡", "🚲", "🛶", "🌵", "🍀", "🍄", "🐚", "🪐", "🌖", "🌪", "⚡️", "🌈", "🔥", "🦁", "🦅", "🐉", "🦖", "🦂", "🦈", "🦍", "🦊", "🦉", "🦄"]
        available_syms = [s for s in all_syms if s not in existing_symbols]
        m_count = st.slider("Members", 2, 9, 2)
        with st.form("registration_form"):
            new_id, new_pass, topic = st.text_input("Team ID"), st.text_input("Password", type="password"), st.text_input("Topic")
            selected_symbol = st.selectbox("Choose Symbol", available_syms) if available_syms else None
            m_names = [st.text_input(f"Member {i+1}", key=f"reg_{i}") for i in range(m_count)]
            if st.form_submit_button("Register"):
                if not selected_symbol: st.error("No symbols available.")
                elif supabase.table("team_itineraries").select("team_name").eq("team_name", new_id).execute().data: st.error("ID taken.")
                else:
                    supabase.table("team_itineraries").insert({"team_name": new_id, "password": hash_password(new_pass), "topic": topic, "members": m_names, "symbol": selected_symbol, "itinerary_data": []}).execute()
                    st.success("Registered! Now you can login.")
    st.stop()

# --- 5. NAVIGATION ---
st.sidebar.title(f"{st.session_state.user_symbol} {st.session_state.user_id}")
if st.sidebar.button("🌏 Master Map", use_container_width=True): st.session_state.view, st.session_state.selected_team = "Live Dashboard", None; st.rerun()
if st.sidebar.button("👤 Team Profile", use_container_width=True): st.session_state.view, st.session_state.selected_team = "Team Profile", st.session_state.user_id; st.rerun()
if st.sidebar.button("📖 Read Me", use_container_width=True):
    st.session_state.view = "Read Me"
    st.rerun()
st.sidebar.divider()
if st.sidebar.button("🔓 Logout", use_container_width=True): st.session_state.authenticated = False; st.rerun()

# --- 6. LIVE DASHBOARD ---
if st.session_state.view == "Live Dashboard":
    teams_res = supabase.table("team_itineraries").select("*").execute()
    if teams_res.data:
        overlaps = find_overlaps(st.session_state.user_id, teams_res.data)
        for ov in overlaps: st.success(f"🤝 You and {ov['team']} are in {ov['location']} from {ov['start'].strftime('%B %d, %H:%M')} to {ov['end'].strftime('%B %d, %H:%M')}")
    m = folium.Map(location=[20.5937, 78.9629], zoom_start=5, tiles="CartoDB positron")
    seen_master = set()
    for t in teams_res.data:
        coords, status, line = get_current_status(deserialize_itinerary(t['itinerary_data']))
        if coords: 
            final_c = get_jittered_coords(coords, seen_master, t['team_name'])
            if line: folium.PolyLine(line, color="orange", weight=2, dash_array='5,5').add_to(m)
            folium.Marker(final_c, icon=folium.DivIcon(html=f'<div style="font-size:12pt;">{get_team_symbol(t)}</div>')).add_to(m)
    st_folium(m, width=None, height=400, use_container_width=True, key="master_map_main")
    for t in teams_res.data:
        _, status, _ = get_current_status(deserialize_itinerary(t['itinerary_data']))
        with st.container(border=True):
            st.markdown(f"**{get_team_symbol(t)} {t['team_name']}**")
            st.markdown(f"<div style='font-size:0.85em; color:gray;'>{status}</div>", unsafe_allow_html=True)
            if st.button("View Profile", key=f"vp_{t['team_name']}", use_container_width=True): st.session_state.selected_team, st.session_state.view = t['team_name'], "Team Profile"; st.rerun()

# --- 7. TEAM PROFILE ---
elif st.session_state.view == "Team Profile":
    target = st.session_state.selected_team if st.session_state.selected_team else st.session_state.user_id
    res = supabase.table("team_itineraries").select("*").eq("team_name", target).execute()
    if res.data:
        data = res.data[0]; itin = deserialize_itinerary(data['itinerary_data']); lc, ls, ll = get_current_status(itin)
        st.title(f"{get_team_symbol(data)} {target}")
        st.info(f"**Topic:** {data['topic']} | **Members:** {', '.join(data['members'])}"); st.success(f"**Current Status:** {ls}")
        m_det = folium.Map(location=[20.5937, 78.9629], zoom_start=5, tiles="CartoDB positron")
        route_coords = [[p['lat'], p['lon']] for p in itin if p['Type'] == "Place" and p.get('lat')]
        for idx, coord in enumerate(route_coords, 1):
            folium.Marker(location=coord, icon=folium.DivIcon(html=f'<div style="background:#2c3e50; color:white; border-radius:50%; width:24px; height:24px; display:flex; align-items:center; justify-content:center; border:2px solid white; font-weight:bold; font-size:12px;">{idx}</div>')).add_to(m_det)
        if len(route_coords) > 1: folium.PolyLine(route_coords, color="#2c3e50", weight=3).add_to(m_det)
        if lc: 
            if ll: folium.PolyLine(ll, color="orange", weight=3, dash_array='5,5').add_to(m_det)
            folium.Marker(location=lc, icon=folium.DivIcon(html=f'<div style="font-size:26pt; filter: drop-shadow(0 0 5px white);">{get_team_symbol(data)}</div>')).add_to(m_det)
        if route_coords or lc: m_det.fit_bounds(route_coords + ([lc] if lc else []))
        st_folium(m_det, width=None, height=350, use_container_width=True, key=f"p_map_{target}")
        for item in itin:
            with st.container(border=True):
                symbol = '📍' if item['Type']=='Place' else '🚆'
                dest_str = f" ➔ {item['Destination']}" if item['Type'] == 'Transit' else ""
                st.markdown(f"**{symbol} {item['Location']}{dest_str}**")
                st.markdown(f"<div style='font-size:0.85em; color:gray;'>{item['Time In'].strftime('%H:%M')}, {item['Day In'].strftime('%d %b')} to {item['Time Out'].strftime('%H:%M')}, {item['Day Out'].strftime('%d %b')}</div>", unsafe_allow_html=True)
        if target == st.session_state.user_id:
            if st.button("✏️ Edit Itinerary", type="primary", use_container_width=True): st.session_state.view, st.session_state.itinerary = "Itinerary Builder", itin; st.rerun()

# --- 8. ITINERARY BUILDER ---
elif st.session_state.view == "Itinerary Builder":
    st.title("🗺️ Builder")
    def render_inline_ui(idx):
        if st.session_state.action_mode == "select_type" and st.session_state.insert_index == idx:
            if st.button("➕ Place", use_container_width=True): st.session_state.action_mode = "add_place"; st.rerun()
            if st.button("🚆 Transit", use_container_width=True): st.session_state.action_mode = "add_transit"; st.rerun()
            if st.button("Cancel", use_container_width=True): st.session_state.action_mode = None; st.rerun()
        def_loc, def_date, def_time = "", datetime.date.today(), datetime.time(0, 0)
        if idx > 0:
            p = st.session_state.itinerary[idx-1]
            def_loc, def_date, def_time = (p['Destination'] if p['Type']=="Transit" else p['Location']), p['Day Out'], p['Time Out']
        if st.session_state.insert_index == idx:
            if st.session_state.action_mode == "add_place":
                with st.form(f"pf_{idx}"):
                    n = st.text_input("Place", value=def_loc); c1, c2 = st.columns(2)
                    d1, t1, d2, t2 = c1.date_input("Day In", value=def_date), c1.time_input("Time In", value=def_time), c2.date_input("Day Out", value=def_date), c2.time_input("Time Out", value=datetime.time(23,59))
                    c_btn1, c_btn2 = st.columns(2)
                    save_place = c_btn1.form_submit_button("✅ Save")
                    cancel_place = c_btn2.form_submit_button("❌ Cancel")

                    if save_place:
                        v, c, la, lo = validate_location(n)
                        if v and is_valid_time_range(d1, t1, d2, t2):
                            st.session_state.itinerary.insert(idx, {"Type":"Place","Location":c,"lat":la,"lon":lo,"Destination":"-","Day In":d1,"Time In":t1,"Day Out":d2,"Time Out":t2})
                            st.session_state.edit_index = idx; resolve_timeline_overlaps(); st.session_state.action_mode = None; st.rerun()
                    
                    if cancel_place:
                        st.session_state.action_mode = None; st.rerun()
            elif st.session_state.action_mode == "add_transit":
                with st.form(f"tf_{idx}"):
                    o, d = st.text_input("Origin", value=def_loc), st.text_input("Destination"); c1, c2 = st.columns(2)
                    d1, t1, d2, t2 = c1.date_input("Day In", value=def_date), c1.time_input("Time In", value=def_time), c2.date_input("Day Out", value=def_date), c2.time_input("Time Out", value=datetime.time(23,59))
                    c_btn1, c_btn2 = st.columns(2)
                    save_transit = c_btn1.form_submit_button("✅ Save")
                    cancel_transit = c_btn2.form_submit_button("❌ Cancel")

                    if save_transit:
                        v1, c1, la1, lo1 = validate_location(o); v2, c2, la2, lo2 = validate_location(d)
                        if v1 and v2 and is_valid_time_range(d1, t1, d2, t2):
                            st.session_state.itinerary.insert(idx, {"Type":"Transit","Location":c1,"lat":la1,"lon":lo1,"Destination":c2,"dest_lat":la2,"dest_lon":lo2,"Day In":d1,"Time In":t1,"Day Out":d2,"Time Out":t2})
                            st.session_state.edit_index = idx; resolve_timeline_overlaps(); st.session_state.action_mode = None; st.rerun()

                    if cancel_transit:
                        st.session_state.action_mode = None; st.rerun()

    for i, item in enumerate(st.session_state.itinerary):
        if st.button(f"➕ Insert Here", key=f"ins_{i}", use_container_width=True): st.session_state.insert_index, st.session_state.action_mode = i, "select_type"; st.rerun()
        render_inline_ui(i)
        is_edit = (st.session_state.action_mode == "edit" and st.session_state.edit_index == i)
        with st.container(border=(not is_edit)):
            c_text, c_edit, c_del = st.columns([7.5, 1.25, 1.25], vertical_alignment="center")
            with c_text:
                symbol = '📍' if item['Type']=='Place' else '🚆'
                dest_str = f" ➔ {item['Destination']}" if item['Type'] == 'Transit' else ""
                st.markdown(f"**{symbol} {item['Location']}{dest_str}**")
                st.markdown(f"<div style='font-size:0.85em; color:gray;'>{item['Time In'].strftime('%H:%M')}, {item['Day In'].strftime('%d %b')} to {item['Time Out'].strftime('%H:%M')}, {item['Day Out'].strftime('%d %b')}</div>", unsafe_allow_html=True)
            if c_edit.button("✏️", key=f"e_{i}"): st.session_state.edit_index, st.session_state.action_mode = i, "edit"; st.rerun()
            if c_del.button("❌", key=f"d_{i}"): st.session_state.itinerary.pop(i); st.session_state.edit_index = max(0, i-1); resolve_timeline_overlaps(); st.rerun()
        if is_edit:
            with st.form(f"ef_{i}"):
                l1, l2 = st.text_input("Location", item['Location']), st.text_input("Destination", item['Destination'])
                c1, c2 = st.columns(2)
                d1, t1, d2, t2 = c1.date_input("Day In", item['Day In']), c1.time_input("Time In", item['Time In']), c2.date_input("Day Out", item['Day Out']), c2.time_input("Time Out", item['Time Out'])
                if st.form_submit_button("Update"):
                    v1, c1, la1, lo1 = validate_location(l1)
                    if v1 and is_valid_time_range(d1, t1, d2, t2):
                        new_item = {"Type": item['Type'], "Location": c1, "lat": la1, "lon": lo1, "Day In": d1, "Time In": t1, "Day Out": d2, "Time Out": t2}
                        if item['Type'] == "Transit":
                            v2, c2, la2, lo2 = validate_location(l2)
                            if v2: new_item.update({"Destination": c2, "dest_lat": la2, "dest_lon": lo2})
                        else: new_item.update({"Destination": "-", "dest_lat": None, "dest_lon": None})
                        st.session_state.itinerary[i] = new_item
                        st.session_state.edit_index = i
                        resolve_timeline_overlaps()
                        st.session_state.action_mode = None; st.rerun()

    if st.button(f"➕ Add to End", key="add_end", use_container_width=True): st.session_state.insert_index, st.session_state.action_mode = len(st.session_state.itinerary), "select_type"; st.rerun()
    render_inline_ui(len(st.session_state.itinerary))
    st.divider()
    if st.button("💾 Save Changes", type="primary", use_container_width=True):
        supabase.table("team_itineraries").update({"itinerary_data": serialize_itinerary(st.session_state.itinerary)}).eq("team_name", st.session_state.user_id).execute()
        st.success("Saved!"); st.rerun()

# --- 9. GLOBAL READ ME ---
elif st.session_state.view == "Read Me":
    st.title("📖 Read Me")
    
    with st.container(border=True):
        st.markdown("""
        ### Instructions.
        This is a global guide for all teams.
        
        1. **Login:** Ensure each team member of the same team logs into the website with the same credentials. Do not create multiple accounts for one team. Please enter the correct team member names when you register.
        2. **Updating your Itinerary:** You can edit your itinerary after entering it once, by clicking on the "Edit Itinerary" button at the bottom of the Team Profile page.
        3. **Automatic Sync:** When editing your itinerary, if you change a time, the steps immediately before and after will shift automatically to prevent gaps or overlaps.
        4. **Overlaps:** If two teams are at the same city/town at the same time, a notification will appear at the top of the "Master Map" tab.
        5. **False Itinerary:** Please do not enter incorrect itineraries on purpose just to play a prank. It will send false alerts to other teams, and it will clutter tha map.
        6. **Location:** If you are in some part of the city, don't enter the specific street or area, enter the city name itself so that the website can alert you of any overlaps.
        
        """)
