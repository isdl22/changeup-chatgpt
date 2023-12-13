
import requests
import json
from typing import Dict, Any, List
from openai import OpenAI
import logging
import time

# Configure basic logging
logging.basicConfig(level=logging.INFO, force=True)
logger = logging.getLogger('MyUniqueZapierLogger')

class ZapierActionAPI:
    def __init__(self, api_key: str, debug: bool = False) -> None:
        self.api_key: str = api_key
        self.debug: bool = debug
        self.headers: Dict[str, str] = {
            'Content-Type': 'application/json',
            'x-api-key': self.api_key
        }
        self.base_url: str = 'https://actions.zapier.com/api/v1'

        # Perform an initial check to verify the API key
        self._check_api_key()

    def _check_api_key(self) -> None:
        """
        Checks if the provided API key is valid.
        """
        try:
            self._make_get_request('/check/')
            logger.info("API key successfully verified.")
        except Exception as e:
            logger.error("Invalid API Key: " + str(e))
            # raise ValueError("Invalid API Key") from e

    def _make_get_request(self, endpoint: str) -> Dict[str, Any]:
        """
        Makes a GET request to a given endpoint.
        """
        url = f'{self.base_url}{endpoint}'
        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            raise requests.RequestException(f"Request failed: {response.status_code}")

        if self.debug:
          print(f"Response JSON for {url}: {json.dumps(response.json(), indent=4)}")

        return response.json()

    def get_actions_list(self) -> Dict[str, Any]:
        """
        Fetches the list of actions using the RESTful list endpoint.
        """
        return self._make_get_request('/dynamic/exposed/')

    def get_openapi_schema(self) -> Dict[str, Any]:
        """
        Fetches the dynamic OpenAPI JSON schema.
        """
        return self._make_get_request('/dynamic/openapi.json')

    def get_action_list_with_hint_parameters(self) -> Dict[str, Any]:
        """
        Fetches a list of an action's optional hint parameters.
        """
        return self._make_get_request('/dynamic/exposed/')

    def get_openapi_schema_with_hint_parameters(self) -> Dict[str, Any]:
        """
        Fetches the dynamic OpenAPI schema for actions.
        """
        return self._make_get_request('/dynamic/openapi.json')

    def get_formatted_tools_from_openapi_schema(self) -> Dict[str, Any]:
        """
        Fetches and formats the OpenAPI schema specifically for exposed AI actions to fit the structure of the provided request JSON.
        """
        openapi_schema = self.get_openapi_schema()
        formatted_tools = []

        for path, path_item in openapi_schema['paths'].items():
            if path.startswith('/api/v1/exposed/') and path.endswith('/execute/'):
                post_method = path_item.get('post', {})
                name = post_method.get('operationId', '') #
                action_id = path.split('/')[4] if len(path.split('/')) > 4 else None # action_id

                # Extracting the reference for the request body schema
                request_body_ref = post_method.get('requestBody', {}).get('content', {}).get('application/json', {}).get('schema', {}).get('$ref', '')

                # Extracting the schema details from components using the reference
                function_parameters = {}
                if request_body_ref:
                    ref_key = request_body_ref.split('/')[-1]
                    schema = openapi_schema['components']['schemas'].get(ref_key, {})
                    function_parameters = {
                        'type': schema.get('type', 'object'),
                        'properties': schema.get('properties', {}),
                        'required': schema.get('required', [])
                    }

                formatted_tools.append({
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": action_id,
                        "parameters": function_parameters
                    }
                })

        if self.debug:
            print(f"Formatted OpenAPI tools: {json.dumps(formatted_tools, indent=4)}")

        return formatted_tools

    def execute_action(self, action_id: str, payload: Dict[str, Any]) -> Any:
        """
        Executes a Zapier action with the given payload.
        """
        url = f'{self.base_url}/dynamic/exposed/{action_id}/execute/'
        response = requests.post(url, headers=self.headers, json=payload)
        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"Failed to execute action: {response.text}")


    def find_function_tool_by_name(self, tool_name: str, assistant):
        """
        Finds a function tool with a given name in the assistant's tools.

        :param tool_name: The name of the function tool to find.
        :return: The found function tool or None if not found.
        """
        # tool_name : google_sheets_find_worksheet_3041dab
        # name : google_sheets_find_worksheet_3041dab
        # description : 01HGKJKBPR3EMCK6BY3V40QMW1 (action_id)
        for tool in assistant.tools:
            if tool.type == 'function' and tool.function.name == tool_name:
                return tool.function
        return None

    def execute_actions_from_assistant(self, assistant_response: Dict[str, Any], assistant_object) -> None:
        """
        Executes Zapier actions based on the tool calls from the Assistant's response.

        :param assistant_response: The response from the Assistant API.
        :param action_id_mapping: A mapping from Assistant function names to Zapier action IDs.
        """
        tool_calls = assistant_response.required_action.submit_tool_outputs.tool_calls
        tool_results = []
        for tool_call in tool_calls:
            logger.info(tool_call)
            tool_name = tool_call.function.name
            function_tool = self.find_function_tool_by_name(tool_name, assistant_object)

            if function_tool:
                action_id = function_tool.description
                arguments = json.loads(tool_call.function.arguments)

                # Using all arguments from the response to construct the payload
                zapier_payload = arguments

                # Execute the action
                try:
                    result = self.execute_action(action_id, zapier_payload)
                    tool_results.append({"tool_call_id": tool_call.id,
                                         "output": json.dumps(result)})
                    logger.info(f"Action executed successfully: {result}")
                except Exception as e:
                    logger.error(f"Error executing action: {str(e)}")
            else:
                logger.error(f"Invalid function URL format: {function_tool}")
        return tool_results


class AssistantAPI():
    def __init__(self, api_key:str, debug: bool = False) -> None:
        self.api_key: str = api_key
        self.debug: bool = debug
        self.client = OpenAI(api_key=api_key)

    def create_assistant(self, zapier_api: ZapierActionAPI, name: str, instructions: str):
        logger.info("Loading allowed Zapier AI Actions...")
        formatted_tools = zapier_api.get_formatted_tools_from_openapi_schema()
        logger.info("Complete loading Zapier AI Actions...")
        try:
            self.assistant = self.client.beta.assistants.create(
                name=name,
                instructions=instructions,
                model="gpt-4-1106-preview",
                tools=formatted_tools
            )
            logger.info(f"Complete creating assistant: {name}")
            return 200
        except Exception as e:
            print(f"Error executing action: {str(e)}")
            return None

    def retrieve_assistant(self, assistant_id):
        try:
            self.assistant = self.client.beta.assistants.retrieve(assistant_id)
            logger.info(f"Complete retrieving assistant: {assistant_id}")
            return 200
        except Exception as e:
            print(f"Error executing action: {str(e)}")
            return None


    def create_thread(self, user_message: str=""):
        messages = []
        if user_message != "":
            messages.append(user_message)

        try:
            self.thread = self.client.beta.threads.create(
              messages=messages
            )
            logger.info("Complete creating thread...")
            return 200
        except Exception as e:
            print(f"Error executing action: {str(e)}")
            return None


    def retrieve_thread(self, thread_id):
        try:
            self.thread = self.client.beta.threads.retrieve(thread_id)
            logger.info(f"Complete retrieving thread: {thread_id}")
            return 200
        except Exception as e:
            print(f"Error executing action: {str(e)}")
            return None

    def create_user_message(self, user_message: str, thread_id: str=""):
        _thread_id = self.thread.id
        if thread_id != "":
            _thread_id = thread_id

        try:
            thread_message = self.client.beta.threads.messages.create(
              _thread_id,
              role="user",
              content=user_message,
            )
            self.current_messages.data.append(thread_message)
            logger.info("Complete creating user message...")
            return 200
        except Exception as e:
            print(f"Error executing action: {str(e)}")
            return None

    def create_assistant_message(self, assistant_message: str, thread_id: str=""):
        # not supported yet...
        _thread_id = self.thread.id
        if thread_id != "":
            _thread_id = thread_id

        try:
            thread_message = self.client.beta.threads.messages.create(
              _thread_id,
              role="assistant",
              content=assistant_message,
            )
            logger.info("Complete creating assistant message...")
        except Exception as e:
            print(f"Error executing action: {str(e)}")


    def retrieve_all_message_of_thread(self, thread_id: str=""):
        _thread_id = self.thread.id
        if thread_id != "":
            _thread_id = thread_id

        try:
            self.current_messages: List = self.client.beta.threads.messages.list(
                thread_id=_thread_id
            )
            logger.info("Complete loading thread messages...")
            return 200
        except Exception as e:
            print(f"Error executing action: {str(e)}")
            return None

    def create_run(self):
        try:
            self.current_run = self.client.beta.threads.runs.create(
                thread_id=self.thread.id,
                assistant_id=self.assistant.id
            )
            logger.info("Complete creating run...")
            return self.current_run
        except Exception as e:
            print(f"Error executing action: {str(e)}")
            return None

    def retrieve_all_runs_of_thread(self, thread_id: str=""):
        _thread_id = self.thread.id
        if thread_id != "":
            _thread_id = thread_id

        try:
            self.runs: List = self.client.beta.threads.runs.list(
              _thread_id
            )
            logger.info("Complete loading runs...")
            return 200
        except Exception as e:
            print(f"Error executing action: {str(e)}")
            return None

    def retrieve_all_run_steps_of_thread(self, run_id: str, thread_id: str=""):
        _thread_id = self.thread.id
        if thread_id != "":
            _thread_id = thread_id

        try:
            self.run_steps: List = self.client.beta.threads.runs.steps.list(
                thread_id=_thread_id,
                run_id=run_id
            )
            logger.info("Complete loading runs...")
            return 200
        except Exception as e:
            print(f"Error executing action: {str(e)}")
            return None

    def check_run_state(self):
        run = self.client.beta.threads.runs.retrieve(
          thread_id=self.thread.id,
          run_id=self.current_run.id
        )
        return run

    def submit_tool_outputs(self, tool_outputs):
        run = self.client.beta.threads.runs.submit_tool_outputs(
          thread_id=self.thread.id,
          run_id=self.current_run.id,
          tool_outputs=tool_outputs
        )
        return run

    def run_assistant(self, zapier_api: ZapierActionAPI):
        if self.create_run():
            running_state = True
            while running_state:
                time.sleep(5)
                current_run = self.check_run_state()
                current_status = current_run.status
                if current_status == "queued":
                    logger.info(f"Run is queued : {self.current_run.id}")
                elif current_status == "in_progress":
                    logger.info(f"Yet run is in Progress : {self.current_run.id}")
                elif current_status == "requires_action":
                    logger.info(f"Waiting action from run : {self.current_run.id}")
                    self.run_zapier_action(current_run, zapier_api)
                elif current_status == "completed":
                    logger.info(f"Complete running run : {self.current_run.id}")
                    running_state = False
                    status_code = self.retrieve_all_message_of_thread()
                elif current_status == "expired":
                    logger.info(f"Run is expired : {self.current_run.id}")
                    running_state = False
                elif current_status == "canceled":
                    logger.info(f"Run is canceled : {self.current_run.id}")
                    running_state = False
                elif current_status == "failed":
                    logger.info(f"Run is failed : {self.current_run.id}")
                    # error 확인
                    running_state = False
            return status_code
        else:
            return

    def run_zapier_action(self, run, zapier_api: ZapierActionAPI):
        tool_outputs = zapier_api.execute_actions_from_assistant(run, assistant_object=self.assistant)
        run = self.submit_tool_outputs(tool_outputs)


ZAPIER_API_KEY: str = "sk-ak-NoB23a7i15so6exmECdPx9JUkL"
OPENAI_API_KEY: str = "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"

try:
    zapier_api = ZapierActionAPI(ZAPIER_API_KEY)
except ValueError as e:
    logger.error(e)

import streamlit as st

st.title("Zapier Assistant Demo")

# ------------ utils function ------------ #
def initialize_streamlit():
    """
    streamlit 초기 상태값들을 세팅하는 함수입니다.
    """
    # Assistant와 관련한 상태값 세팅
    if 'assistant' not in st.session_state or st.session_state['assistant'] is None:
        assistant_api = AssistantAPI(OPENAI_API_KEY)
        st.session_state.assistant = assistant_api
    else:
        assistant_api = st.session_state.assistant

    # 메시지와 관련한 상태값 세팅
    if 'messages' not in st.session_state or st.session_state['messages'] is None:
        st.session_state.messages = []

    # 로딩과 관련한 상태값 세팅
    if 'is_running' not in st.session_state:
        st.session_state['is_running'] = False

    return assistant_api

def check_current_page():
    """
    streamlit에서 렌더링할 페이지를 확인하는 함수입니다.
    """
    if 'assistant_id' not in st.session_state or st.session_state['assistant_id'] is None:
        return "setting"
    elif 'assistant_id' in st.session_state and st.session_state['assistant_id'] is not None:
        return "chat"

def check_thread():
    """
    streamlit에서 이전에 대화하던 thread가 존재하는지 확인하는 함수입니다.
    """
    return 'thread_id' not in st.session_state or st.session_state['thread_id'] is None

def mapping_messages(assistant_api: AssistantAPI):
    """
    Assistant와 대화하던 메시지를 streamlit에서 렌더링하기 위해 데이터를 가공하는 함수입니다.
    """
    messages = []
    for message in assistant_api.current_messages.data:
        role = "assistant" if message.role == "assistant" else "user"

        content = message.content[0].text.value if message.content else ""

        messages.append({
            "role": role,
            "content": content
        })
    st.session_state.messages = messages

# ------------ event function ------------ #
def connect_assistant(assistant_id):
    """
    기존에 만들어놓은 Assistant를 연결할 때 사용하는 함수입니다.
    - [Assistant 연결하기] 버튼을 누르면 실행됩니다.
    - 기존의 생성한 Assistant 의 id를 복사해서 넣으면, 정보를 가져와서 연결합니다.
    """

    # 이미 생성한 zapier assistant가 있는 경우 불러오기
    status_code = assistant_api.retrieve_assistant(assistant_id)

    if status_code == 200:
        # 불러온 zapier assistant를 UI에 업데이트
        st.session_state.assistant_id = assistant_api.assistant.id
        st.toast("Assistant를 성공적으로 연결했어요!", icon='🤖')



def create_assistant(assistant_name, assistant_instructions):
    """
    새롭게 Assistant를 만들 때 사용하는 함수입니다.
    - [Assistant 생성하기] 버튼을 누르면 실행됩니다.
    - 기존 GPTs에서 사용한 이름, 지시(instruction)를 그대로 가져오시면 됩니다.
    - 여기서 GPTs와 Assistant API의 차이가 하나 있는데,
    - Assistant API에서는 Instructions for Zapier Custom Action, REQUIRED_ACTIONS 부분은 빼는게 더 잘 동작합니다!
    """

    # 입력한 값으로 Zapier Assistant 생성하기
    status_code = assistant_api.create_assistant(zapier_api, assistant_name, assistant_instructions)
    if status_code == 200:
        # 생성한 assistant UI에 업데이트
        st.session_state.assistant_id = assistant_api.assistant.id
        st.toast("Assistant를 성공적으로 만들었어요!", icon='🤖')


def run_assistant():
    """
    Assistant를 실행하는 사용하는 함수입니다.
    - 채팅창에 메시지를 입력하면, 실행되는 함수입니다.
    - 먼저 유저의 메시지를 thread에 넣습니다. (대화를 담는 그릇입니다.)
    - 그리고 run을 실행합니다. run은 Assistant가 혼자서 생각을 하면서 일을 수행하는 하나의 단위입니다.
    - run은 run step으로 나뉘는데 처리해야 하는 일이 많으면 여러 번 실행되기도 합니다.
    """

    # 로딩 UI를 위한 설정
    st.session_state['is_running'] = True

    with st.spinner("Assistant is processing..."):
      prompt = st.session_state.prompt

      # assistant thread에 메시지 추가하기
      status_code = assistant_api.create_user_message(prompt, st.session_state.get('thread_id'))

      if status_code == 200: # 성공적으로 유저의 메시지 추가
          # UI에 메시지 추가하기
          st.session_state.messages.append({
              "role": "user",
              "content": prompt
          })
          st.toast("메시지가 성공적으로 전송되었어요.", icon='📬')

          # assistant run 실행하기 # 매우 중요!
          status_code = assistant_api.run_assistant(zapier_api)
          if status_code == 200:
              mapping_messages(assistant_api)

    st.session_state['is_running'] = False

# ------------ UI Rendering ------------ #
# Streamlit에 Assistant 설정
assistant_api = initialize_streamlit()
current_page = check_current_page()


# Assistant를 설정하는 페이지
if current_page == "setting":
    # [Assistant 연결하기] UI 렌더링
    with st.container():
        st.subheader('기존에 만들어놓은 Assistant 연결하기', divider='rainbow')
        assistant_id = st.text_input("Assistant 연결하기", placeholder="assistant id를 입력해주세요..")
        st.button("Assistant 연결하기", on_click=connect_assistant, args=(assistant_id,))


# [Assistant 생성하기] UI 렌더링
if current_page == "setting":
    # gpts에서 만든 zapier 봇 가져오기
    with st.container():
        st.subheader('Assistant 새로 만들기', divider='rainbow')
        assistant_name = st.text_input("Assistant 이름", placeholder="Assistant의 이름을 입력하세요...")
        assistant_instructions = st.text_area("Instructions", placeholder="Assistant의 instruction을 입력하세요...")
        st.button("Assistant 생성하기", on_click=create_assistant, args=(assistant_name, assistant_instructions))


# Assistant를 사용하는 페이지
if current_page == "chat":
    # thread가 없으면 자동으로 thread 생성하기
    if check_thread():
        # 새로운 thread 생성하기
        status_code = assistant_api.create_thread()

        if status_code == 200:
            st.session_state['thread_id'] = assistant_api.thread.id
            st.toast('새로운 대화 쓰레드가 성공적으로 생성되었어요!', icon='✅')

    st.subheader(f'{assistant_api.assistant.name}', divider='rainbow')

    # 설정된 thread가 있으면 모든 메시지 가져오기
    status_code = assistant_api.retrieve_all_message_of_thread()


    if status_code == 200:
        # 이전의 메시지를 출력하는 부분
        mapping_messages(assistant_api)

    # 메시지 렌더링하기
    if 'messages' in st.session_state:
        for message in st.session_state.messages[::-1]:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])


    # Assistant에게 메시지를 보내는 UI 로직
    disabled = st.session_state.get('is_running', False)
    st.chat_input(placeholder="메시지를 입력하세요.", on_submit=run_assistant, key="prompt", disabled=disabled)

