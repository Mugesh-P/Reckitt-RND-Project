from flask import Flask, render_template, request, redirect, session, url_for, jsonify, send_file
from msal import ConfidentialClientApplication
from azure.storage.blob import BlobServiceClient
import tempfile, os, re
from datetime import datetime
from dotenv import dotenv_values, find_dotenv
import sys 
sys.path.append('../')
value_env = dotenv_values('.env')



app = Flask(__name__)
# app = Flask(__name__, template_folder='.')
app.secret_key = os.urandom(24)

# ---------------- Azure Config ---------------- #
CLIENT_ID = value_env['CLIENT_ID']
CLIENT_SECRET = value_env['CLIENT_SECRET']
TENANT_ID = value_env['TENANT_ID']
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
REDIRECT_PATH = "/getAToken"
SCOPE = ["User.Read"]

# Blob Storage Config
storage_account_key = value_env['AZURE_STORAGE_ACCOUNT_KEY']
storage_account_name = value_env['AZURE_STORAGE_ACCOUNT_NAME']
CONNECTION_STRING = value_env['ADLS_CONNECTION_STRING']
CONTAINER_NAME = value_env['CONTAINER_NAME']
blob_name = "PDF/PySpark Certificate.pdf" 

# MSAL Client
msal_app = ConfidentialClientApplication(
    CLIENT_ID, authority=AUTHORITY, client_credential=CLIENT_SECRET
)


# ---------------- Login Flow ---------------- #
@app.route('/')
def index():
    if not session.get("user"):
        return render_template("login.html")
    return redirect(url_for("dashboard"))


@app.route("/login")
def login():
    auth_url = msal_app.get_authorization_request_url(
        scopes=SCOPE,
        redirect_uri=url_for("authorized", _external=True)
    )
    return redirect(auth_url)


@app.route(REDIRECT_PATH)
def authorized():
    code = request.args.get("code")
    if not code:
        return "Login failed: no authorization code returned"

    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=SCOPE,
        redirect_uri=url_for("authorized", _external=True)
    )

    print(result)
    if "id_token_claims" in result:
        claims = result["id_token_claims"]
        session["user"] = {
            "name": claims.get("name"),
            "email": claims.get("preferred_username"),
            "roles": claims.get("roles", []),  # Entra app roles
        }
        return redirect(url_for("dashboard"))

    return "Authentication failed"


@app.route("/logout")
def logout():
    session.clear()
    return redirect(
        f"{AUTHORITY}/oauth2/v2.0/logout?post_logout_redirect_uri={url_for('index', _external=True)}"
    )


# ---------------- Dashboard Routing ---------------- #
@app.route('/dashboard')
def dashboard():
    if not session.get("user"):
        return redirect(url_for("login"))
    user = session["user"]
    return render_template("dashboard.html", user=user)


@app.route('/upload-page')
def upload_page():
    if not session.get("user"):
        return redirect(url_for("login"))
    return render_template("upload.html", user=session["user"], title="Upload File")


@app.route('/search-page')
def search_page():
    if not session.get("user"):
        return redirect(url_for("login"))
    return render_template("search.html", user=session["user"], title="Search Files")


@app.route('/users-page')
def users_page():
    if not session.get("user"):
        return redirect(url_for("login"))
    if "Admin" not in session["user"].get("roles", []):
        return "Unauthorized", 403
    return render_template("users.html", user=session["user"], title="User Management")


# ---------------- Blob Operations ---------------- #
@app.route('/upload', methods=['POST'])
def upload_file():
    file = request.files.get('file')
    name = request.form.get('name')
    filename = request.form.get('filename')
    description = request.form.get('description')
    tags = request.form.getlist('tags')

    blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    blob_client = blob_service.get_blob_client(container=CONTAINER_NAME, blob=f"uploads/{file.filename}")

    metadata = {
        "UploaderName": name,
        "FileName": filename,
        "FileType": file.filename.split('.')[-1],
        "Description": description,
        "Tags": ','.join(tags),
        "TimeStamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    blob_client.upload_blob(file, overwrite=True)
    blob_client.set_blob_metadata({k: str(v) for k, v in metadata.items()})
    return "File uploaded successfully!"


@app.route('/search', methods=['GET'])
def search_files():
    query = request.args.get('query', '').lower()
    blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    container_client = blob_service.get_container_client(CONTAINER_NAME)

    results = []
    for blob in container_client.list_blobs(include=['metadata']):
        meta = blob.metadata or {}
        searchable_text = " ".join([str(v).lower() for v in meta.values()])
        if query in searchable_text or re.search(query, blob.name, re.IGNORECASE):
            meta["BlobName"] = blob.name
            results.append(meta)

    return jsonify(results)


@app.route('/download')
def download_file():
    blob_name = request.args.get('blob')
    if not blob_name:
        return "Blob name missing", 400

    blob_service = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    blob_client = blob_service.get_blob_client(container=CONTAINER_NAME, blob=blob_name)

    with tempfile.NamedTemporaryFile(delete=False) as temp:
        temp.write(blob_client.download_blob().readall())
        temp_path = temp.name

    return send_file(temp_path, as_attachment=True, download_name=os.path.basename(blob_name))


# ---------------- Admin User Management ---------------- #
@app.route('/manage-users', methods=['POST'])
def manage_users():
    print(session.get("user", {}).get("roles", []))
    if "Admin" not in session.get("user", {}).get("roles", []):
        return "Unauthorized", 403

    username = request.form.get("username")
    role = request.form.get("role")
    # TODO: integrate Microsoft Graph API for real Entra user management
    print(f"Admin action: {username} -> {role}")
    return f"{username} assigned as {role}"


if __name__ == "__main__":
    app.run(debug=True)
    # app.run(host="localhost", port=5000, debug=True)

