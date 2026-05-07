import streamlit as st
import datetime
import folium
from streamlit_folium import st_folium
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut
from supabase import create_client, Client
import pytz
import hashlib

# --- 1. SETUP & CONFIG ---
st.set_page_config(page_title="Explorer: Master Portal", layout="wide")
IST = pytz.timezone('Asia/Kolkata')
geolocator = Nominatim(user_agent="explorer_master_final_v15")

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

# --- 3. HELPER FUNCTIONS ---
def hash_password(password):
    return hashlib.sha256(str.encode(password)).hexdigest()

def get_team_symbol(team_data):
    return team_data.get('symbol', "🚩")

def validate_location(location_name):
    if location_name == "-": return True, location_name, None, None
    try:
        location = geolocator.geocode(location_name, country_codes="in")
        if location: return True, location_name.title(), location.latitude, location.longitude 
        return False, None, None, None
    except: return False, None, None, None

def resolve_timeline_overlaps():
    for i in range(1, len(st.session_state.itinerary)):
        prev, curr = st.session_state.itinerary[i-1], st.session_state.itinerary[i]
        prev_end = datetime.datetime.combine(prev['Day Out'], prev['Time Out'])
        st.session_state.itinerary[i]['Day In'] = prev_end.date()
        st.session_state.itinerary[i]['Time In'] = prev_end.time()
        curr_end = datetime.datetime.combine(st.session_state.itinerary[i]['Day Out'], st.session_state.itinerary[i]['Time Out'])
        if prev_end >= curr_end:
            new_end = prev_end + datetime.timedelta(hours=1)
            st.session_state.itinerary[i]['Day Out'] = new_end.date()
            st.session_state.itinerary[i]['Time Out'] = new_end.time()

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
        new_item['Day In'] = datetime.date.fromisoformat(item['Day In'])
        new_item['Day Out'] = datetime.date.fromisoformat(item['Day Out'])
        new_item['Time In'] = datetime.datetime.strptime(item['Time In'], '%H:%M:%S').time()
        new_item['Time Out'] = datetime.datetime.strptime(item['Time Out'], '%H:%M:%S').time()
        deserialized.append(new_item)
    return deserialized

def get_current_status(itinerary):
    now = datetime.datetime.now(IST).replace(tzinfo=None)
    if not itinerary: return None, "No Itinerary", None
    first_step_start = datetime.datetime.combine(itinerary[0]['Day In'], itinerary[0]['Time In'])
    if now < first_step_start: return None, "⏳ Journey hasn't started yet", None
    for item in itinerary:
        start, end = datetime.datetime.combine(item['Day In'], item['Time In']), datetime.datetime.combine(item['Day Out'], item['Time Out'])
        if start <= now <= end:
            until_str = f"until {end.strftime('%H:%M, %d %b')}"
            if item['Type'] == "Place":
                return (item['lat'], item['lon']), f"📍 {item['Location']} {until_str}", None
            mid_lat, mid_lon = (item['lat'] + item['dest_lat']) / 2, (item['lon'] + item['dest_lon']) / 2
            line = [[item['lat'], item['lon']], [item['dest_lat'], item['dest_lon']]]
            return (mid_lat, mid_lon), f"🚆 {item['Location']} ➔ {item['Destination']} {until_str}", line
    return None, "⏳ Journey Ended", None

def find_overlaps(current_team_id, all_teams_data):
    my_team_data = next((t for t in all_teams_data if t['team_name'] == current_team_id), None)
    if not my_team_data: return []
    my_itin = deserialize_itinerary(my_team_data['itinerary_data'])
    overlaps = []
    for other in all_teams_data:
        if other['team_name'] == current_team_id: continue
        other_itin = deserialize_itinerary(other['itinerary_data'])
        for m in my_itin:
            if m.get('Type') != "Place": continue
            for o in other_itin:
                if o.get('Type') != "Place": continue
                if m['Location'].strip().lower() == o['Location'].strip().lower():
                    m_s, m_e = datetime.datetime.combine(m['Day In'], m['Time In']), datetime.datetime.combine(m['Day Out'], m['Time Out'])
                    o_s, o_e = datetime.datetime.combine(o['Day In'], o['Time In']), datetime.datetime.combine(o['Day Out'], o['Time Out'])
                    s_lap, e_lap = max(m_s, o_s), min(m_e, o_e)
                    if s_lap < e_lap:
                        overlaps.append({'team': other['team_name'], 'location': m['Location'], 'start': s_lap, 'end': e_lap})
    return overlaps

# --- 4. AUTHENTICATION ---
if not st.session_state.authenticated:
    st.title("🛡️ Explorer Authentication")
    tab1, tab2 = st.tabs(["Login", "Register"])
    with tab1:
        with st.form("login"):
            l_id, l_pw = st.text_input("Team ID"), st.text_input("Password", type="password")
            if st.form_submit_button("Enter"):
                res = supabase.table("team_itineraries").select("*").eq("team_name", l_id).execute()
                if res.data and res.data[0]['password'] == hash_password(l_pw):
                    st.session_state.update({"authenticated": True, "user_id": l_id, "user_symbol": get_team_symbol(res.data[0]), "view": "Live Dashboard"})
                    st.rerun()
                else: st.error("Access Denied")
    with tab2:
        st.warning("⚠️ No recovery option exists for IDs/Passwords.")
        existing_res = supabase.table("team_itineraries").select("symbol").execute()
        existing_symbols = [row.get("symbol") for row in existing_res.data if row.get("symbol")] if existing_res.data else []
        all_syms = ["🛟", "🛞", "🔮", "⌚️", "🗿", "🏀","🎱","🌎","🌖","🎩","🚗","🚕","🚙","🚌","🏎","🚜","🏍","🛺","🚡", "🚁","🚀","🛸","🚢","🎠","🐫","🐂","🦜","🦖"]
        available_syms = [s for s in all_syms if s not in existing_symbols]
        m_count = st.slider("Members", 2, 9, 2)
        with st.form("registration"):
            new_id, new_pass, topic = st.text_input("Team ID"), st.text_input("Password", type="password"), st.text_input("Topic")
            selected_symbol = st.selectbox("Choose Symbol", available_syms) if available_syms else None
            m_names = [st.text_input(f"Member {i+1}", key=f"reg_{i}") for i in range(m_count)]
            if st.form_submit_button("Register"):
                if not selected_symbol: st.error("No symbols available.")
                elif supabase.table("team_itineraries").select("team_name").eq("team_name", new_id).execute().data: st.error("ID taken.")
                else:
                    supabase.table("team_itineraries").insert({"team_name": new_id, "password": hash_password(new_pass), "topic": topic, "members": m_names, "symbol": selected_symbol, "itinerary_data": []}).execute()
                    st.success("Registered! Go to Login tab.")
    st.stop()

# --- 5. NAVIGATION ---
st.sidebar.title(f"{st.session_state.user_symbol} {st.session_state.user_id}")
if st.sidebar.button("🌏 Master Map", use_container_width=True):
    st.session_state.view, st.session_state.selected_team = "Live Dashboard", None; st.rerun()
if st.sidebar.button("👤 Team Profile", use_container_width=True):
    st.session_state.view, st.session_state.selected_team = "Team Profile", st.session_state.user_id; st.rerun()
if st.sidebar.button("🔓 Logout", use_container_width=True):
    st.session_state.authenticated = False; st.rerun()

# --- 6. LIVE DASHBOARD ---
if st.session_state.view == "Live Dashboard":
    teams_res = supabase.table("team_itineraries").select("*").execute()
    if teams_res.data:
        for ov in find_overlaps(st.session_state.user_id, teams_res.data):
            st.success(f"🚨 ALERT: You and {ov['team']} are in {ov['location']} from {ov['start'].strftime('%H:%M, %d %b')} to {ov['end'].strftime('%H:%M, %d %b')}!")
    
    m = folium.Map(location=[22, 82], zoom_start=5, tiles="CartoDB positron")
    for t in teams_res.data:
        coords, status, line = get_current_status(deserialize_itinerary(t['itinerary_data']))
        if coords:
            if line: folium.PolyLine(line, color="orange", weight=2, dash_array='5,5').add_to(m)
            folium.Marker(coords, icon=folium.DivIcon(html=f'<div style="font-size:14pt;">{get_team_symbol(t)}</div>')).add_to(m)
    st_folium(m, width=None, height=400, use_container_width=True)
        
    for t in teams_res.data:
        _, status, _ = get_current_status(deserialize_itinerary(t['itinerary_data']))
        with st.container(border=True):
            st.markdown(f"**{get_team_symbol(t)} {t['team_name']}**")
            st.markdown(f"<div style='font-size:0.9em; color:gray;'>{status}</div>", unsafe_allow_html=True)
            if st.button("View Profile", key=f"v_{t['team_name']}", use_container_width=True):
                st.session_state.selected_team, st.session_state.view = t['team_name'], "Team Profile"; st.rerun()

# --- 7. TEAM PROFILE ---
elif st.session_state.view == "Team Profile":
    target = st.session_state.selected_team or st.session_state.user_id
    res = supabase.table("team_itineraries").select("*").eq("team_name", target).execute()
    if res.data:
        data = res.data[0]
        st.title(f"{get_team_symbol(data)} {target}")
        itin = deserialize_itinerary(data['itinerary_data'])
        m_det = folium.Map(location=[22, 82], zoom_start=5, tiles="CartoDB positron")
        pts = [[p['lat'], p['lon']] for p in itin if p['Type'] == "Place"]
        for idx, p in enumerate(pts):
            folium.Marker(p, icon=folium.DivIcon(html=f'<div style="background:#2c3e50;color:white;border-radius:50%;width:18px;height:18px;display:flex;align-items:center;justify-content:center;font-size:9px;font-weight:bold;">{idx+1}</div>')).add_to(m_det)
        if len(pts) > 1: folium.PolyLine(pts, color="#2c3e50", weight=2, opacity=0.6, dash_array='5,5').add_to(m_det)
        
        lc, ls, ll = get_current_status(itin)
        if lc:
            if ll: folium.PolyLine(ll, color="orange", weight=3).add_to(m_det)
            folium.Marker(lc, icon=folium.DivIcon(html=f'<div style="font-size:25pt;">{get_team_symbol(data)}</div>')).add_to(m_det)
        st_folium(m_det, width=None, height=350, use_container_width=True)

        for i, item in enumerate(itin):
            with st.container(border=True):
                # FIXED: Added Bold and Emoji formatting here
                lbl = f"📍 {item['Location']}" if item['Type'] == 'Place' else f"🚆 {item['Location']} ➔ {item['Destination']}"
                st.markdown(f"**{i+1}. {lbl}**")
                st.markdown(f"<div style='font-size:0.8em; color:gray; margin-top:-10px;'>{item['Time In'].strftime('%H:%M, %d %b')} - {item['Time Out'].strftime('%H:%M, %d %b')}</div>", unsafe_allow_html=True)

        if target == st.session_state.user_id:
            if st.button("✏️ Edit Itinerary", type="primary", use_container_width=True):
                st.session_state.view, st.session_state.itinerary = "Itinerary Builder", itin; st.rerun()

# --- 8. BUILDER ---
elif st.session_state.view == "Itinerary Builder":
    st.title("🗺️ Builder")
    for i, item in enumerate(st.session_state.itinerary):
        is_edit = (st.session_state.action_mode == "edit" and st.session_state.edit_index == i)
        with st.container(border=(not is_edit)):
            c_text, c_edit, c_del = st.columns([7.5, 1.25, 1.25])
            with c_text:
                lbl = f"📍 {item['Location']}" if item['Type'] == 'Place' else f"🚆 {item['Location']} ➔ {item['Destination']}"
                st.markdown(f"**{i+1}. {lbl}**")
                st.markdown(f"<div style='font-size:0.8em; color:gray; margin-top:-10px;'>{item['Time In'].strftime('%H:%M, %d %b')} to {item['Time Out'].strftime('%H:%M, %d %b')}</div>", unsafe_allow_html=True)
            if c_edit.button("✏️", key=f"e_{i}"): st.session_state.edit_index, st.session_state.action_mode = i, "edit"; st.rerun()
            if c_del.button("❌", key=f"d_{i}"): st.session_state.itinerary.pop(i); resolve_timeline_overlaps(); st.rerun()
    
    st.divider()
    ca, cb = st.columns(2)
    if ca.button("➕ Place", use_container_width=True): st.session_state.action_mode = "add_place"
    if cb.button("🚆 Transit", use_container_width=True): st.session_state.action_mode = "add_transit"

    if st.session_state.action_mode in ["add_place", "add_transit"]:
        pos_opts = ["At the end"]
        for j, step in enumerate(st.session_state.itinerary):
            lbl = f"{step['Location']} ➔ {step['Destination']}" if step['Type'] == "Transit" else step['Location']
            pos_opts.append(f"After {lbl} (Step {j+1})")
            
        selected_pos = st.selectbox("Position", pos_opts)
        idx, def_d, def_t = len(st.session_state.itinerary), datetime.date.today(), datetime.time(0,0)
        if "After" in selected_pos:
            idx = int(selected_pos.split("(Step ")[1].split(")")[0])
            prev = st.session_state.itinerary[idx-1]
            def_d, def_t = prev['Day Out'], prev['Time Out']

        if st.session_state.action_mode == "add_place":
            with st.form("pf"):
                n = st.text_input("Place Name")
                c1, c2 = st.columns(2)
                d1, t1 = c1.date_input("In", def_d), c1.time_input("T1", def_t)
                d2, t2 = c2.date_input("Out", def_d), c2.time_input("T2", datetime.time(23,59))
                if st.form_submit_button("Save"):
                    v, c, la, lo = validate_location(n)
                    if v:
                        st.session_state.itinerary.insert(idx, {"Type":"Place","Location":c,"lat":la,"lon":lo,"Destination":"-","Day In":d1,"Time In":t1,"Day Out":d2,"Time Out":t2})
                        resolve_timeline_overlaps(); st.session_state.action_mode = None; st.rerun()
        
        elif st.session_state.action_mode == "add_transit":
            with st.form("tf"):
                o, d = st.text_input("Origin"), st.text_input("Destination")
                c1, c2 = st.columns(2)
                d1, t1 = c1.date_input("Dep", def_d), c1.time_input("T1", def_t)
                d2, t2 = c2.date_input("Arr", def_d), c2.time_input("T2", datetime.time(23,59))
                if st.form_submit_button("Save"):
                    v1, c1, la1, lo1 = validate_location(o)
                    v2, c2, la2, lo2 = validate_location(d)
                    if v1 and v2:
                        st.session_state.itinerary.insert(idx, {"Type":"Transit","Location":c1,"lat":la1,"lon":lo1,"Destination":c2,"dest_lat":la2,"dest_lon":lo2,"Day In":d1,"Time In":t1,"Day Out":d2,"Time Out":t2})
                        resolve_timeline_overlaps(); st.session_state.action_mode = None; st.rerun()

    elif st.session_state.action_mode == "edit":
        it = st.session_state.itinerary[st.session_state.edit_index]
        with st.form("ef"):
            l1 = st.text_input("Location", it['Location'])
            l2 = st.text_input("Destination", it['Destination']) if it['Type']=="Transit" else "-"
            c1, c2 = st.columns(2)
            d1, t1 = c1.date_input("In", it['Day In']), c1.time_input("T1", it['Time In'])
            d2, t2 = c2.date_input("Out", it['Day Out']), c2.time_input("T2", it['Time Out'])
            if st.form_submit_button("Update"):
                v, c, la, lo = validate_location(l1)
                if v:
                    st.session_state.itinerary[st.session_state.edit_index].update({"Location":c,"lat":la,"lon":lo,"Day In":d1,"Time In":t1,"Day Out":d2,"Time Out":t2})
                    if it['Type']=="Transit":
                        v2, c2, la2, lo2 = validate_location(l2)
                        if v2: st.session_state.itinerary[st.session_state.edit_index].update({"Destination":c2,"dest_lat":la2,"dest_lon":lo2})
                    resolve_timeline_overlaps(); st.session_state.action_mode = None; st.rerun()
    
    st.divider()
    if st.button("💾 Cloud Save", type="primary", use_container_width=True):
        supabase.table("team_itineraries").update({"itinerary_data": serialize_itinerary(st.session_state.itinerary)}).eq("team_name", st.session_state.user_id).execute()
        st.success("Synced!")