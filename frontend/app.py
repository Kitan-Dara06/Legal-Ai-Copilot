import streamlit as st
import httpx
import os
import time

API_URL = os.getenv("API_URL", "http://localhost:8000")
ORG_ID = "stream_ui_org"

st.set_page_config(
    page_title="Legal AI Copilot",
    page_icon="⚖️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for aesthetics
st.markdown("""
<style>
    .stChatFloatingInputContainer {
        padding-bottom: 2rem;
    }
    .main-header {
        font-family: 'Inter', sans-serif;
        color: #1E3A8A;
        font-weight: 700;
        margin-bottom: 0px;
    }
    .sub-header {
        font-family: 'Inter', sans-serif;
        color: #6B7280;
        margin-top: 0px;
        margin-bottom: 2rem;
    }
</style>
""", unsafe_allow_html=True)

# Initialization
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "available_files" not in st.session_state:
    st.session_state.available_files = []

def refresh_files():
    try:
        res = httpx.get(f"{API_URL}/files/list", params={"org_id": ORG_ID})
        if res.status_code == 200:
            st.session_state.available_files = res.json().get("files", [])
    except Exception as e:
        st.error(f"Backend connection error: {e}")

# Sidebar: File Management
with st.sidebar:
    st.header("📂 Document Library")
    
    # Upload Section
    uploaded_files = st.file_uploader("Upload New Contracts", type=["pdf"], accept_multiple_files=True)
    if st.button("Upload to Backend", use_container_width=True) and uploaded_files:
        with st.spinner("Uploading & Processing..."):
            files_payload = [("files", (f.name, f.read(), "application/pdf")) for f in uploaded_files]
            try:
                res = httpx.post(f"{API_URL}/files/upload", files=files_payload, params={"org_id": ORG_ID}, timeout=120.0)
                if res.status_code == 202:
                    st.success("Uploaded successfully! Processing in background...")
                    time.sleep(1) # wait a moment for initial DB flush
            except Exception as e:
                st.error(f"Upload failed: {e}")
                
    st.divider()
    
    # File View Section
    st.subheader("Available Files")
    if st.button("🔄 Refresh List", use_container_width=True):
        refresh_files()
        
    if not st.session_state.available_files:
        st.info("No formatted documents available. Upload one to begin.")
    else:
        for f in st.session_state.available_files:
            file_id = f["file_id"]
            fname = f["filename"]
            st.caption(f"📄 {fname} (ID: {file_id})")
            
    st.divider()
    if st.session_state.session_id:
        st.success(f"🟢 Active Session:\n\n`{st.session_state.session_id[:8]}...`")
        
        # Display files currently inside the active session
        try:
            res = httpx.get(f"{API_URL}/session/{st.session_state.session_id}", timeout=10.0)
            if res.status_code == 200:
                session_data = res.json()
                session_files = session_data.get("files", [])
                st.markdown("**Files in Context:**")
                for f in session_files:
                    status_emoji = "⏳" if f['status'] == 'PROCESSING' else "✅"
                    fname_display = f.get('filename', 'Unknown File')
                    col_name, col_btn = st.columns([4, 1])
                    with col_name:
                        st.caption(f"{status_emoji} {fname_display}")
                    with col_btn:
                        if st.button("❌", key=f"remove_{f['file_id']}", help=f"Remove {fname_display} from session"):
                            try:
                                httpx.delete(
                                    f"{API_URL}/session/{st.session_state.session_id}/files/{f['file_id']}",
                                    timeout=10.0
                                )
                                st.toast(f"Removed {fname_display} from session.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Could not remove file: {e}")
        except Exception as e:
            st.error("Could not load session files.")
            
        st.divider()
        
        st.markdown("**Add Document to Session**")
        session_upload = st.file_uploader("Upload directly to active chat", type=["pdf"], key="session_uploader")
        if st.button("Upload to Session", use_container_width=True) and session_upload:
            with st.spinner("Processing & embedding into session..."):
                files_payload = {"file": (session_upload.name, session_upload.read(), "application/pdf")}
                try:
                    res = httpx.post(
                        f"{API_URL}/session/{st.session_state.session_id}/upload", 
                        files=files_payload, 
                        params={"org_id": ORG_ID},
                        timeout=120.0
                    )
                    if res.status_code in [200, 202]:
                        st.success("File added to active session!")
                        time.sleep(1)
                        st.rerun()
                    else:
                        st.error(f"Failed to upload: {res.text}")
                except Exception as e:
                    st.error(f"Upload failed: {e}")

        st.divider()
        if st.button("🛑 Terminate Session", type="primary", use_container_width=True):
            try:
                httpx.delete(f"{API_URL}/session/{st.session_state.session_id}")
            except:
                pass
            st.session_state.session_id = None
            st.session_state.messages = []
            st.rerun()

# Main Interface
st.markdown("<h1 class='main-header'>⚖️ Legal AI Copilot</h1>", unsafe_allow_html=True)
st.markdown("<p class='sub-header'>Chat seamlessly with your securely embedded corporate contracts.</p>", unsafe_allow_html=True)

# State 1: No active session
if not st.session_state.session_id:
    st.info("Initialize a secure workspace session to begin chatting.")
    
    if st.session_state.available_files:
        file_options = {f["file_id"]: f["filename"] for f in st.session_state.available_files}
        selected_file_ids = st.multiselect(
            "Select documents to include in this session's context:",
            options=list(file_options.keys()),
            format_func=lambda x: file_options[x]
        )
        
        if st.button("🚀 Create Workspace", type="primary") and selected_file_ids:
            with st.spinner("Initializing Workspace..."):
                try:
                    res = httpx.post(f"{API_URL}/session/", json=selected_file_ids, params={"org_id": ORG_ID}, timeout=120.0)
                    if res.status_code in [200, 201]:
                        st.session_state.session_id = res.json().get("session_id")
                        st.session_state.messages = [
                            {"role": "assistant", "content": "I'm ready. I have fully indexed the selected contracts. What would you like to know?"}
                        ]
                        st.rerun()
                    else:
                        st.error(f"Failed to create session: {res.text}")
                except Exception as e:
                    st.error(f"API Error: {e}")

# State 2: Active Session
else:
    # Top Bar: Inference Mode Switcher
    col1, col2 = st.columns([1, 4])
    with col1:
        use_agentic = st.toggle("🤖 Agentic Tool Router", value=False, help="Enable multi-step planning and tool usage. Slower but better for complex logic.")
    with col2:
        st.write("") # spacing

    # Chat History
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            
    # Chat Input
    if prompt := st.chat_input("Ask a question about your contracts..."):
        # Append user message
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
            
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            
            # Formulate request
            endpoint = "/ask-agent" if use_agentic else "/ask"
            q_params = {
                "session_id": st.session_state.session_id,
                "question": prompt,
                "org_id": ORG_ID,
                "mode": "hybrid" if use_agentic else "fast"
            }
            
            try:
                with st.spinner("Analyzing..." if not use_agentic else "Agent Planning..."):
                    res = httpx.post(f"{API_URL}{endpoint}", params=q_params, timeout=60.0)
                    res.raise_for_status()
                    
                    # Handle varying response structures
                    if use_agentic:
                        answer = res.json().get("answer", "No answer provided")
                    else:
                        answer = res.json().get("answer", "No answer provided")
                        
                    message_placeholder.markdown(answer)
                    st.session_state.messages.append({"role": "assistant", "content": answer})
                    
            except Exception as e:
                st.error(f"Inference error: {e}")
