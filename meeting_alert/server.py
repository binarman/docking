busy_flag = True

def getMessagePage(flag):
  message = "MEETING" if busy_flag else "FREE"
  color = "red" if busy_flag else "green"
  
  content = f"""
<!DOCTYPE html>
<html>
<head>
<meta http-equiv="refresh" content="5">
<link rel="stylesheet" href="styles.css">
</head>
<body style="background-color:{color};">

<p>
{message}
</p>

</body>
</html>
"""
  content_type = "text/html"
  return (content_type, content)

def getCSS():
  content = """
p {
  text-align: center;
  line-height: 0%;
  font-size: 50.0vmin;
}
"""
  content_type = "text/css"
  return (content_type, content)

def getIcon():
  content = ""
  content_type = "text/plain"
  return (content_type, content)


def app(environ, start_response):
    path = environ.get("PATH_INFO", "")
    print("requested page:", path)
    global busy_flag
    if path == "/busy":
      busy_flag = True
      content = "status set to busy"
      content_type = "text/plain"
    elif path == "/free":
      busy_flag = False
      content = "status set to free"
      content_type = "text/plain"
    elif path == "/":
      content_type, content = getMessagePage(busy_flag)
    elif path == "/styles.css":
      content_type, content = getCSS()
    elif path == "/favicon.ico":
      content_type, content = getIcon()
    else:
      content = "invalid request: try \"busy\" or \"free\""
      content_type = "text/plain"
      
    data = str.encode(content)
    start_response("200 OK", [
        ("Content-Type", content_type),
        ("Content-Length", str(len(data)))
    ])
    return iter([data])
