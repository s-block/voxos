// CloudFront Function (cloudfront-js-2.0 runtime) for the website distribution's
// viewer-request event. The site is built with trailing-slash directory URLs,
// and a private S3 origin behind Origin Access Control has no index documents:
//   - `/docs/` is served from `/docs/index.html`
//   - `/docs` redirects to `/docs/`
//   - `www.` hosts redirect to the bare domain, in the same single redirect
// Requests for files (`/robots.txt`, `/_astro/app.js`) pass through unchanged.

/* exported handler */
function handler(event) {
  var request = event.request;
  var uri = request.uri;
  var host = request.headers.host.value.toLowerCase();
  var lastSegment = uri.slice(uri.lastIndexOf("/") + 1);
  // Extensionless paths are directories missing their trailing slash.
  var path = lastSegment === "" || lastSegment.indexOf(".") !== -1 ? uri : uri + "/";

  if (host.indexOf("www.") === 0) {
    return redirect("https://" + host.slice(4) + path + queryString(request.querystring));
  }
  if (path !== uri) {
    return redirect(path + queryString(request.querystring));
  }
  if (lastSegment === "") {
    request.uri = uri + "index.html";
  }
  return request;
}

function redirect(location) {
  return {
    statusCode: 301,
    statusDescription: "Moved Permanently",
    headers: { location: { value: location } },
  };
}

function queryString(params) {
  var pairs = [];
  for (var key in params) {
    var param = params[key];
    var values = param.multiValue ? param.multiValue : [param];
    for (var i = 0; i < values.length; i++) {
      pairs.push(values[i].value === "" ? key : key + "=" + values[i].value);
    }
  }
  return pairs.length > 0 ? "?" + pairs.join("&") : "";
}
