#!/usr/bin/python
# Taken from:
# https://github.com/ansible/ansible/blob/05c6ff79f9860dbd6b43cb4914ee749baf65b9f7/lib/ansible/modules/cloud/amazon/sqs_queue.py
# Copyright: Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import absolute_import, division, print_function
__metaclass__ = type

import re
import time

try:
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    pass    # Handled by AnsibleAWSModule

from ansible.module_utils.aws.core import AnsibleAWSModule


ANSIBLE_METADATA = {'metadata_version': '0.1',
                    'status': ['preview'],
                    'supported_by': 'community'}


DOCUMENTATION = """
---
module: sqs_message
short_description: Send / receive events from an AWS SQS queue.
description:
  - Send events into an SQS queue
  - Receive events from an SQS queue
version_added: "2.0"
author:
  - Jose Fernandez
requirements:
  - "boto >= 2.33.0"
options:
  action:
    description:
      - Action to perform (send / receive)
      - Note messages will not be sent in check mode. To force sending
        messages use check_mode: no.
      - When a message is sent, the changed flag is set.
    required: false
    default: receive
  body:
    description:
      - Body of the message.
      - Only used when action="send"
    required: false
  delay_seconds:
    description:
      - SQS DelaySeconds, ie: the time that SQS shall wait before delivering
        a message.
      - Only used when action="send"
    required: false
    default: 0
  delete:
    description:
      - Delete all the messages received.
      - Only used when action="receive"
      - When using filter_regex, the messages rejected by the filter are not
        deleted.
      - Note messages will not be deleted in check mode. To force deleting
        messages use check_mode: no.
      - When a message is deleted, the changed flag is set.
    required: false
    default: false
  filter_regex:
    description:
      - Filter to apply to the body of the received messages. Only messages
        matching the filter will be returned.
      - Only used when action="receive"
      - When filter_regex is specified, only the matching part of the message
        is returned. To return the whole message, use a filter line
        '.*pattern.*'.
      - When using return_on_reception, messages that do not match the
        filter_regex will be ignored, and the task will only return when a
        message matching filter_regex is received or the wait_seconds lapse.
  queue_name:
    description:
      - Name of the queue.
    required: true
  region:
    description:
      - AWS region to use
    required: false
  return_on_reception:
    description:
      - Return as soon as the first message(s) are received, without waiting
        the remaining wait_time.
      - Note more than one message might be returned if they were in the
        queue or received simultaneously.
    required: false
    default: receive
  visibility_timeout_seconds:
    description:
      - SQS visibility timeout, ie: messages received are hidden from other
        receivers for as long as specified.
      - Only used when action="receive"
      - Note if delete=false and visibility_timeout_seconds<wait_seconds, the
        same message may be received more than once.
    required: false
    default: 30
  wait_seconds:
    description:
      - The time to wait when receiving messages.
      - Only used when action="receive"
      - wait_seconds=0 will return any messages available in the queue without
        waiting more.
      - Note messages waiting their visibility timeout may not be received.
      - Note the AWS documentation states that for wait_seconds=0 not all
        events might be returned.
    required: false
    default: 0
extends_documentation_fragment:
    - aws
"""

RETURN = '''
messages:
    description:
      - When action="receive", the bodies of the messages received. If no messages were received, returns an empty list.
      - When action="send", the messages sent in a list of dict, as returned by AWS.
    type: list of str or list of dict
    returned: always
    sample: ["received message 1 body", "received message 2 body"]
'''

EXAMPLES = '''
# Ensure that CloudWatch does not report errors during a canary test period
 - name: flush the CloudWatch alert message queue to ignore errors that happened before the update
    sqs_message:
      queue_name: cw-problem-alarm-queue
      region: us-east-1
      wait_seconds: 0
      delete: true
    check_mode: no

  - name: wait out the canary quarantine time and make sure no new errors are received
    sqs_message:
      queue_name: cw-problem-alarm-queue
      region: us-east-1
      wait_seconds: 300
      return_on_reception: true
      filter_regex: "[Ee]rror"
      delete: true
    register: result
    failed_when: result.failed or (result.messages | length) > 0
    check_mode: no
'''


def sqs_receive_messages(module, queue):
    """ Receive SQS messages. """
    queue_name = module.params.get('queue_name')

    result = dict(
        region=module.params.get('region'),
        queue_name=queue_name,
        messages=[],
    )

    if module.params.get('filter_regex'):
        try:
            filter_regex = re.compile(module.params.get('filter_regex'))
        except re.error as e:
            module.fail_json_aws(e, msg='Error parsing filter_regex')
    else:
        filter_regex = None

    try:
        start_time = time.time()
        end_time = start_time + module.params.get('wait_seconds')
        while True:
            messages = queue.receive_messages(
                MaxNumberOfMessages=10,
                WaitTimeSeconds=min(max(int(end_time-time.time()), 0), 20),
                VisibilityTimeout=module.params.get('visibility_timeout_seconds'))

            num_messages_processed = 0
            for msg in messages:
                body = msg.body

                if filter_regex:
                    match = filter_regex.search(body)
                    if not match:
                        continue
                    else:
                        body = match.group(0)

                result['messages'] += [body]

                if module.params.get('delete') and \
                    not module.check_mode:
                    msg.delete()
                    result['changed'] = True

                num_messages_processed += 1

            if (not messages and time.time() > end_time) or \
                (num_messages_processed > 0 and \
                module.params.get('return_on_reception')):
                break

    except (BotoCoreError, ClientError) as e:
        module.fail_json_aws(e, msg='Error')
    else:
        module.exit_json(**result)


def sqs_send_message(module, queue):
    """ Send SQS message. """
    queue_name = module.params.get('queue_name')

    result = dict(
        region=module.params.get('region'),
        queue_name=queue_name,
    )

    if module.check_mode:
        module.exit_json(**result)
        return

    try:
        message = queue.send_message(
            MessageBody=module.params.get('body'),
            DelaySeconds=module.params.get('delay_seconds'))
        result["messages"] = [message]

    except (BotoCoreError, ClientError) as e:
        module.fail_json_aws(e, msg='Error')
    else:
        module.exit_json(**result)


def main():
    """ Main entry point for Ansible. """
    argument_spec = dict(
        action=dict(required=False, type='str', default='receive'),
        body=dict(required=False, type='str'),
        delay_seconds=dict(type='int', default=0),
        delete=dict(type='bool', required=False, default=False),
        filter_regex=dict(required=False, type='str'),
        queue_name=dict(required=True, type='str'),
        region=dict(required=True, type='str'),
        return_on_reception=dict(type='bool', default=False),
        visibility_timeout_seconds=dict(type='int', default=30),
        wait_seconds=dict(type='int', default=0),
    )

    module = AnsibleAWSModule(argument_spec=argument_spec, supports_check_mode=True)

    queue_name = module.params.get('queue_name')
    try:
        resource = module.resource('sqs')
        queue = resource.get_queue_by_name(QueueName=queue_name)
        if not queue:
            module.fail_json_aws(msg='Failed to find the sqs queue "{}"'.format(queue_name))
    except (BotoCoreError, ClientError) as e:
        module.fail_json_aws(e, msg='Error fetching the queue')

    else:
        action = module.params.get('action')
        if action == 'receive':
            sqs_receive_messages(module, queue)
        elif action == 'send':
            sqs_send_message(module, queue)
        else:
            module.fail_json_aws(msg='Action "{}" not recognized.'.format(action))

if __name__ == '__main__':
    main()
