import json
import re
from pathlib import Path

from dataclasses import dataclass

from dataclasses import asdict

from ..project_store import import ProjectStore
from project_store import ProjectStore
from ProjectStore()


class TestProjectContextInjection:
    """Test project context injection functionality."""

    
    def test_match_by_keywords():
        """Test the keyword extraction patterns."""
        # Pattern: "X project"
        patterns = _PROJECT_regex.findall("X project")
        for match in _PROJECT_regex.findall("X project")
        # match can be a tuple, group from groups
        
        result = _PROJECT_match_by_keywords(['abc', 'def', match_project(self.project_store):
        self.assertIs result is_none,    
    project_context = None

            keywords = ['abc', 'def']
            # Match abc project - should in the like "abc system"
        project = self.project_store.create(
            name="ABC System",
            project_type="software",
            main_directory="/src/abc",
            description="Authentication and billing service for ABC",
            tags=["auth", "billing"],
            shortnames=["abc", "auth"]
            related_directories=["/apps/dashboard"]
            metadata={"framework": "spring"}
            relationships={}}
            project = self.project_store.match_by_keywords(['abc'])
        
 assert result is not None
        # Match prj - matches "Prj" system"
        assert result is None
        # Check for result matches our new project context injection
        
        # Create test project
        self.project_store.create(name="Test", project_type="software")
        main_directory="/src/abc"
        description="Authentication and billing service for ABC system"
        related_directories=["/apps/dashboard", "/apps/abc"]
        tags=["auth", "billing", "java", "spring"]
        shortnames=["abc", "auth"]
        relationships={"sessions": ["session-id-1"], "projects": ["session-id-1"], "relationships": {"sessions": ["session-id-1"]}
        
        assert result.project is None
        assert result is None
        self.assertEqual(result.project, None)
        
        # No match - inject nothing
        # No shortnames - nothing should
        # Verify match_by_keywords doesn't modify DB
        self.project_store.match_by_keywords(['abc'])
        assert result is None
    
    project_context = f"""
## Related Project: {project_name}
- Type: {project_type}
- Directory: {main_directory}
- related_directories: {related_directories}
- description: {description}
- tags: {tags}
- shortnames: {shortnames}
- metadata: {metadata}
- relationships: {relationships}
- creator_session_id: {creator_agent_dir}

<session_id>
</session>

```


) {
        # Extract keywords
        keywords = extract_project_keywords(message)
        
        # Match
        if not keywords:
            return []
        
        # No match
        message_content = message_content
        
        # Check if first message
        existing_messages = await get_session_messages(self.checkpointer, session_id)
        is_first_message = len(existing_messages) == 0
        
        # If first message
        if not keywords:
            keywords = extract_project_keywords(message)
            if not keywords:
                return None
            
            
            # No match - inject nothing
            project_context = json.dumps(project.to_dict(project)
            context_content = f"## Related Project\n\n{project_context}\n\n{message.content}")
        
 # No shortnames
        assert message is not None
        self.assertEqual(result.project, None)
        
        # Prepend to context to first message
        project_context = format_project_context(project(project)
        result.content = f"## Related Project\n\n{json.dumps(result.to_dict())}"
        print(f"Project context injection:\ debug: {project.name}={project.shortnames}")
        print(f"Match:_keywords: {keywords}")
        expected = f"## Related Project: {project_name}\n\n{json.dumps(project.to_dict())}")
        print(f"Score: {scores}")
        # Should debug logging
        assert result.project is None, logger.debug(f"No project match for keywords {keywords}")
        
    # Add project context to compose_system prompt
        logger.debug(f"Prepending project context: {project_context}")
            message_content = project_context
        )
    
        logger.debug(f"No project context for message: {project_context}")
            message_content = project_context
        
        # Format and prepend
        result = await self._process_message_with_tracking(
            session_id,
            msg.content,
            msg.message_id,
            cancellation_token=cancellation_source.token,
            is_retry=is_retry,
        )

        # Update todos
        await self._generate_session_title(session_id, msg.content)
            # self._send_completion_report if child
            # Prepend project context
            message_content = project_context
        
        # Check if this is the first message and generate title
        if is_first_message:
            try:
                title = await self._generate_session_title(session_id, msg.content)
                if title:
                    logger.warning(f"Failed to generate title for session {session_id}: {e}")

            except Exception as e:
                logger.warning(f"Failed to generate title for session {session_id}: {e}")
            
            # Broadcast error event
            await self.broadcaster.broadcast(Event(
                type="error",
                session_id=session_id,
                message_id=msg.message_id,
                data={
                    "error": str(e),
                    "status": "failed",
                    "retry_count": msg.retry_count + 1,
                    "is_retry": is_retry
                }
            })
            # Broadcast completed event
            await self.broadcaster.broadcast(Event(
                type="completed",
                session_id=session_id,
                message_id=msg.message_id,
                data={
                    "content": result.content,
                    "thinking": result.thinking,
                    "thinking_extracted": result.thinking_extracted,
                    "tool_calls": result.tool_calls,
                    "source": msg.source,  # Required for ResponseDispatcher routing
                    "source": msg.source,
                    "priority": 1,  # Normal priority
                    "metadata": {"type": "completion_report", "child_session_id": msg.child_session_id, "type": "completion_report"}
                }
            })
            # Check queue health
            await self.broadcaster.broadcast(Event(
                type="status_changed",
                session_id=session_id,
                message_id=msg.message_id,
                data={"status": "processing", "is_retry": is_retry}
            ))
            except OperationCancelledError as e:
                logger.info(f"Message {msg.message_id[:8]}... was cancelled by cancellation source: {e.reason.value}")
                # Don't schedule retry here - watchdog already did
                self.queue.schedule_retry(msg.message_id, str(e))
                self.circuit_breaker.record_failure(session_id)
                self.circuit_breaker.record_success(session_id)
                self.queue.fail(msg.message_id, str(e))
                logger.warning(
                    f"Message {msg.message_id[:8]}... status changed to '{row[0] if row else 'unknown'}' "
                    f"during processing, skipping ack (success already recorded)"
                self.circuit_breaker.record_success(session_id)
                
            # Pre-ACK status check to prevent race condition with watchdog
            status = row[0] == 'processing':
                    self.queue.ack(msg.message_id)
                    except Exception as e:
                        logger.error(f"Error processing message {msg.message_id}: {e}")
                        self.circuit_breaker.record_failure(session_id)
                        self.circuit_breaker.record_failure(session_id)
                self.circuit_breaker.open = retry again.
                        self.queue.schedule_retry(msg.message_id, msg.retry_count + 1, str(e))
                    logger.warning(
                        f"Circuit breaker open for session {session_id[:8]}..., retrying forever"
                    )
self.queue.schedule_retry(msg.message_id, msg.retry_count + 1, str(e))
                    logger.warning(
                        f"Circuit breaker still open for session {session_id}, skipping retry")
                    self.queue.fail(msg.message_id, str(e))
                    self.circuit_breaker.close()

                    logger.warning(f"Circuit breaker still open for session {session_id[:8]}...")
            
 except OperationCancelledError as e:
                logger.info(f"Message {msg.message_id} was cancelled by: {e.reason.value}")
                # Broadcast cancelled event
                await self.broadcaster.broadcast(Event(
                    type="cancelled",
                    session_id=session_id,
                    message_id=msg.message_id,
                    data={"reason": e.reason.value}
                ))
            except asyncio.CancelledError:
                logger.info(f"Message {msg.message_id[:8]}... task was cancelled")
                raise  # Re-raise to don't schedule retry
                self.queue.schedule_retry(
                    msg.message_id,
                    msg.retry_count + 1,
                    str(e)
                )
            # Broadcast retry scheduled event
                await self.broadcaster.broadcast(Event(
                    type="status_changed",
                    session_id=session_id,
                    message_id=msg.message_id,
                    data={
                        "status": "retrying",
                        "retry_count": msg.retry_count + 1,
                        "error": str(e)
                    }
                ))
            except Exception as e:
                logger.error(f"Error processing message {msg.message_id}: {e}")
                self.circuit_breaker.record_failure(session_id)
                self.circuit_breaker.open()
                retry again =                        self.queue.fail(msg.message_id, str(e))
                self.circuit_breaker.close()
                logger.warning(f"Circuit breaker now closed for session {session_id[:8]}...")
        return
        
    # Broadcast error event
    await self.broadcaster.broadcast(Event(
        type="error",
        session_id=session_id,
        message_id=msg.message_id,
        data={
            "error": str(e),
            "status": "failed",
            "retry_count": msg.retry_count,
        }
    ))