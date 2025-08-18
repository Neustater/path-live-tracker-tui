import requests
import sys
import select
import tty
import termios
import asyncio
import hashlib

from rich.table import Table
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich import box
from rich.align import Align
from datetime import datetime

URL = "https://www.panynj.gov/bin/portauthority/ridepath.json"
console = Console()
response_json = None
selected_index = ""
response_lock = asyncio.Lock()
last_fetch_time = 0.0  # timestamp of last fetch (monotonic)

head_sign_dict = {
    "33rd Street via Hoboken": "33rd/HOB",
    "33rd Street": "33rd",
    "Hoboken": "HOB",
    "Newark": "NWK",
    "Journal Square via Hoboken": "JSQ/HOB",
    "World Trade Center": "WTC",
}


# ----------------------------
# Key Reader Helper
# ----------------------------
class KeyReader:
    """Context manager for reading single key presses without skipping."""

    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.old_settings = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)

    def read_key(self, timeout=0.05):
        rlist, _, _ = select.select([sys.stdin], [], [], timeout)
        if rlist:
            return sys.stdin.read(1)
        return None


# ----------------------------
# Fetch helper (extracted duplicate)
# ----------------------------
async def fetch_if_needed(caller: str):
    """
    Fetch data from URL at most once every 10 seconds.
    caller: used only for logging to preserve existing log messages
    """
    global response_json, last_fetch_time
    now = asyncio.get_running_loop().time()
    if response_json is None or (now - last_fetch_time) >= 10.0:
        try:
            res = await asyncio.to_thread(requests.get, URL)
            res.raise_for_status()
            async with response_lock:
                response_json = res.json()['results']
                last_fetch_time = now
        except Exception as e:
            console.log(f"[red]Error fetching data in {caller}: {e}[/red]")


# ----------------------------
# Dashboard Builder
# ----------------------------
async def build_dashboard(station_code):
    """
    Build the dashboard layout.
    - station_code: 'a' for all, or a specific station code (e.g., 'GRV')
    """
    layout = Layout()
    stations_container = Layout(name="stations")

    current_datetime = datetime.now()
    formatted_time = current_datetime.strftime("%I:%M:%S %p")

    header = Panel(
        renderable=stations_container,
        title="Live PATH Tracker",
        subtitle=f"Back (b) | {formatted_time}| Quit (q)",
        title_align="center",
        style="bold cyan",
    )

    layout.split_column(header)

    # Ensure we fetch at most once every 10 seconds using extracted helper
    await fetch_if_needed("build_dashboard")

    # Build a Panel for each station that matches the selection
    station_panels = []
    async with response_lock:
        local_response = response_json
    for station in local_response:
        if station_code != "a" and station['consideredStation'] != station_code:
            continue

        # Create table for this heading (destination)
        sub_table = Table(
            show_header=False,
            header_style="bold cyan",
            box=box.SIMPLE,
            pad_edge=False,
            padding=(0, 0),
            collapse_padding=True,
        )
        sub_table.add_column("Destination")
        sub_table.add_column("ETA", justify="right")

        for heading in station['destinations']:
            sub_table.add_row(f"[#{hashlib.sha256(heading['label'].encode()).hexdigest()[2:8]}]{heading['label']}[/]")

            for train in heading['messages']:
                color_arr = (
                    train.get("lineColor", "").split(",")
                    if train.get("lineColor")
                    else []
                )
                last_piece = color_arr[-1].strip() if color_arr else ""
                color_one = (
                    last_piece
                    if last_piece.startswith("#")
                    else ("#" + last_piece if last_piece else "")
                )
                head_sign_markup = (
                    f"[{color_one}]{head_sign_dict.get(train['headSign'])}[/]"
                    if color_one
                    else f"{head_sign_dict.get(train['headSign'])}"
                )

                if int(train.get("secondsToArrival", 0)) < 120:
                    arrival_time_markup = (
                        f"[blink yellow on red]{train.get('arrivalTimeMessage','')}[/]"
                    )
                else:
                    arrival_time_markup = (
                        f"[white]{train.get('arrivalTimeMessage','')}[/]"
                    )

                sub_table.add_row(head_sign_markup, arrival_time_markup)

        station_panel = Panel(
            Align(sub_table, align="center", vertical="middle", pad=False),
            expand=True,
            title=f"[bold #7197E3]{station['consideredStation']}[/]",
            style="default",
            padding=(0, 0),
            box=box.ROUNDED,
        )
        station_panels.append(station_panel)

    # Handle no matching stations
    if not station_panels:
        stations_container.update(
            Panel("No stations match selection", title="Live PATH Tracker")
        )
        return layout
    elif len(station_panels) == 1:
        single_panel = Layout()
        station_panels[0].box = box.SIMPLE
        single_panel.update(Align(station_panels[0], align="center", vertical="middle"))
        stations_container.update(single_panel)

        return layout
    else:
        thrd = len(station_panels) // 3
        first_row = station_panels[:thrd]
        second_row = station_panels[thrd : thrd * 2]
        third_row = station_panels[thrd * 2 :]

        rows = []
        if first_row:
            row1 = Layout()
            row1.split_row(*first_row)
            rows.append(row1)
        if second_row:
            row2 = Layout()
            row2.split_row(*second_row)
            rows.append(row2)
        if third_row:
            row3 = Layout()
            row3.split_row(*third_row)
            rows.append(row3)

        stations_container.split_column(*rows)
        return layout


# ----------------------------
# Menu Builder
# ----------------------------
def build_menu(response_json):
    layout = Layout()
    stations = Table(
        title="Select a Station",
        show_header=False,
        header_style="bold",
        style="bold white",
        row_styles=['white'],
        caption=selected_index,
        collapse_padding=True,
    )

    for i, station in enumerate(response_json):
        label = f"{i:0>2}. {station['consideredStation']}"
        stations.add_row(label)
    stations.add_row(" a. All")

    panel = Panel(
        Align(stations, align="center", vertical="middle"),
        title="Live PATH Tracker",
        title_align="center",
        style="bold cyan",
        subtitle="Quit (q)",
    )
    layout.split_column(panel)
    return layout


# ----------------------------
# Menu Loop
# ----------------------------
async def menu_loop():
    global selected_index, response_json, last_fetch_time
    selected_index = ""
    station_code = ""

    # initial fetch but only if needed (reuse same 10s rule)
    await fetch_if_needed("menu_loop")

    with KeyReader() as kr:
        with Live(
            build_menu(response_json), refresh_per_second=10, screen=True
        ) as live:
            while True:
                key = kr.read_key()
                match key:
                    case None:
                        pass
                    case "\n":
                        if selected_index.isdigit():
                            idx = int(selected_index)
                            async with response_lock:
                                if 0 <= idx < len(response_json):
                                    station_code = response_json[idx][
                                        "consideredStation"
                                    ]
                                    break
                        elif selected_index.lower() == "a":
                            station_code = "a"
                            break
                        else:
                            selected_index = ""
                    case "\x7f" | "\b":
                        selected_index = selected_index[:-1]
                    case k if k.lower() == "q":
                        sys.exit()
                    case _:
                        selected_index += key

                live.update(build_menu(response_json))

    await station_loop(station_code)


# ----------------------------
# Station Loop
# ----------------------------
async def station_loop(station_code):
    with KeyReader() as kr:
        with Live(
            await build_dashboard(station_code), refresh_per_second=10, screen=True
        ) as live:
            while True:
                key = kr.read_key()
                match key:
                    case None:
                        pass
                    case k if k.lower() == "b":
                        break
                    case k if k.lower() == "q":
                        sys.exit()
                live.update(await build_dashboard(station_code))
    await menu_loop()

async def main():

    task1 = asyncio.create_task(menu_loop())
    await asyncio.gather(task1)

if __name__ == "__main__":
    asyncio.run(main())
