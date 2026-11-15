"""Agent 经 `location.select_image` 采用地点图片：与人类选择同一字段与校验。"""

import asyncio

from structured_director_fixtures import add_image_asset, install_location
from test_external_script_proposals import agent, http

from core.director_commands import DirectorCommand
from core.structured_director.models import ReferenceGenerationKind
from scripts.harness.fixtures.candidates import register_image_candidate


async def _command(store, service_, name, key, arguments):
    document = store.read()
    command = DirectorCommand(name, document.revision, key, arguments)
    return await http(service_, agent(document, command), command)


def test_agent_selects_location_image_like_human_selection(tmp_path):
    from test_prompt_worksets import fixture

    async def run():
        store, service_, _document, _ = await fixture(tmp_path)
        document = install_location(store)
        image = document.location_library.locations[-1].images[0]
        candidate = add_image_asset(store, width=1920, height=1080)
        register_image_candidate(
            store,
            kind=ReferenceGenerationKind.LOCATION_IMAGE,
            target_id=image.id,
            asset_id=candidate,
        )

        result = await _command(
            store,
            service_,
            "location.select_image",
            "agent-location-pick",
            {"image_id": image.id, "asset_id": candidate},
        )
        assert result.status == 200, result.body
        selected = store.read().location_library.image_owner(image.id)[1]
        assert selected.selected_asset_id == candidate

        unknown = await _command(
            store,
            service_,
            "location.select_image",
            "agent-location-unknown",
            {"image_id": image.id, "asset_id": "f" * 32},
        )
        assert unknown.status != 200
        assert store.read().location_library.image_owner(image.id)[1].selected_asset_id == (
            candidate
        )

    asyncio.run(run())
