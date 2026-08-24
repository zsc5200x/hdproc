use std::io::{Read, Write};
use std::net::{TcpStream, ToSocketAddrs};
use std::time::Duration;

pub const WEBUI_HOST: &str = "127.0.0.1";
pub const WEBUI_PORT: u16 = 9999;
pub const WEBUI_URL: &str = "http://127.0.0.1:9999/";

fn successful_status_line(response: &[u8]) -> bool {
    let line = response
        .split(|byte| *byte == b'\n')
        .next()
        .unwrap_or_default();
    line.starts_with(b"HTTP/1.1 2") || line.starts_with(b"HTTP/1.0 2")
}

pub fn webui_ready() -> bool {
    let address = match (WEBUI_HOST, WEBUI_PORT).to_socket_addrs() {
        Ok(mut addresses) => match addresses.next() {
            Some(address) => address,
            None => return false,
        },
        Err(_) => return false,
    };

    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_millis(700)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(1200)));
    let request = format!(
        "GET /api/status HTTP/1.1\r\nHost: {WEBUI_HOST}:{WEBUI_PORT}\r\nConnection: close\r\n\r\n"
    );
    if stream.write_all(request.as_bytes()).is_err() {
        return false;
    }

    let mut response = [0_u8; 1024];
    match stream.read(&mut response) {
        Ok(size) => successful_status_line(&response[..size]),
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::successful_status_line;

    #[test]
    fn accepts_only_successful_http_statuses() {
        assert!(successful_status_line(b"HTTP/1.1 200 OK\r\n"));
        assert!(successful_status_line(b"HTTP/1.0 204 No Content\r\n"));
        assert!(!successful_status_line(b"HTTP/1.1 404 Not Found\r\n"));
        assert!(!successful_status_line(b"garbage"));
    }
}
