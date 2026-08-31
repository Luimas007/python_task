import requests
import streamlit as st

API_URL = "http://localhost:8000/api"

st.set_page_config(page_title="Samsung Phone Assistant", layout="wide")
st.title("Samsung Galaxy Phone Assistant")

tab_chat, tab_browse = st.tabs(["Chat", "Browse Phones"])

with tab_chat:
    query = st.text_input("Ask about specs, reviews, or comparisons")
    if st.button("Send") and query:
        with st.spinner("Thinking..."):
            resp = requests.post(f"{API_URL}/chat", json={"query": query})
        if resp.ok:
            data = resp.json()
            st.markdown(data["answer"])
            if data["phones"]:
                st.caption(f"Referenced: {', '.join(data['phones'])}")
            st.download_button("Download answer", data["answer"], file_name="answer.txt")
        else:
            st.error(f"Request failed: {resp.status_code}")

with tab_browse:
    resp = requests.get(f"{API_URL}/phones")
    if resp.ok:
        phones = resp.json()
        names = [p["name"] for p in phones]
        selected = st.selectbox("Select a phone", names) if names else None
        if selected:
            phone = next(p for p in phones if p["name"] == selected)
            st.json(phone["specification"])
            if st.button("Generate review"):
                with st.spinner("Generating..."):
                    r = requests.post(f"{API_URL}/phones/{phone['id']}/review")
                if r.ok:
                    review = r.json()["review"]
                    st.markdown(review)
                    st.download_button("Download review", review, file_name=f"{selected}_review.txt")
    else:
        st.warning("Could not reach API. Is it running?")
