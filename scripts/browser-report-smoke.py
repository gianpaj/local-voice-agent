"""Check the actual generated offline report in Chromium, without model calls."""
import argparse
import asyncio
import json
from pathlib import Path
import tempfile

from playwright.async_api import async_playwright


async def main(args):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, executable_path=args.executable, args=["--no-sandbox"])
        try:
            page = await browser.new_page(viewport={"width": 1440, "height": 1100})
            errors, requests = [], []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("request", lambda request: requests.append(request.url) if request.url.startswith("http") else None)
            await page.goto(args.report.resolve().as_uri())
            data = await page.locator("#report-data").text_content()
            dataset = json.loads(data)
            assert await page.locator("#rows tr").count() >= 1
            assert await page.locator("#chart .fill").count() >= 1
            await page.locator("#detail details").first.locator("summary").first.click()
            await page.get_by_role("button", name="Framework", exact=True).click()
            assert "Rustvani" in await page.locator("#detail").inner_text()
            assert "Not measured" in await page.locator("#detail").inner_text()
            await page.get_by_role("button", name="All", exact=True).click()
            await page.locator("#group").select_option("")
            assert await page.locator("#rows tr").count() == len(dataset["comparisons"])
            assert await page.locator("#chart .fill").count() == 0, "Mixed experiment boundaries were ranked together"
            await page.locator("#search").fill("whistle")
            assert await page.locator("#rows tr").count() == 1
            await page.locator("#rows button").click()
            assert "quality-preserving" not in await page.locator("#detail").inner_text() or "not a" in await page.locator("#detail").inner_text()
            await page.locator("#search").fill("no-such-model")
            assert "No comparisons" in await page.locator("#chart").inner_text()
            await page.locator("#search").fill("")
            with tempfile.TemporaryDirectory() as directory:
                async with page.expect_download() as event:
                    await page.get_by_role("button", name="Download comparison JSON").click()
                download = await event.value
                target = Path(directory) / "data.json"
                await download.save_as(target)
                assert json.loads(target.read_text()) == dataset
                async with page.expect_download() as event:
                    await page.get_by_role("button", name="Download metrics CSV").click()
                target = Path(directory) / "metrics.csv"
                await (await event.value).save_as(target)
                assert "boundary" in target.read_text().splitlines()[0]
            await page.screenshot(path=str(args.report.parent / "report-desktop.png"), full_page=True)
            await page.set_viewport_size({"width":390,"height":844})
            assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows horizontally"
            await page.screenshot(path=str(args.report.parent / "report-mobile.png"), full_page=True)
            assert not errors, errors
            assert not requests, requests
            print(json.dumps({"configurations":len(dataset["comparisons"]),"components":len({r["component"] for r in dataset["comparisons"]}),"offline_requests":len(requests),"js_errors":errors,"mixed_boundary_chart_blocked":True,"fixture_drilldown":True,"filters_and_json_csv_exports":True,"mobile_no_overflow":True}))
        finally:
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--executable")
    asyncio.run(main(parser.parse_args()))
