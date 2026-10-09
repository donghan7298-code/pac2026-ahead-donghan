// Fixed-joint mechanism follows Gazebo Fortress DetachableJoint (Apache-2.0):
// https://github.com/gazebosim/gz-sim/blob/ign-gazebo6/src/systems/detachable_joint/DetachableJoint.cc
// This separate AHEAD plugin starts detached, acknowledges idempotent requests
// and publishes after the physics update. It is not a suction pressure model.
#include <atomic>
#include <chrono>
#include <memory>
#include <string>
#include <ignition/gazebo/EntityComponentManager.hh>
#include <ignition/gazebo/Model.hh>
#include <ignition/gazebo/System.hh>
#include <ignition/gazebo/components/DetachableJoint.hh>
#include <ignition/gazebo/components/Link.hh>
#include <ignition/gazebo/components/Model.hh>
#include <ignition/gazebo/components/Name.hh>
#include <ignition/gazebo/components/ParentEntity.hh>
#include <ignition/msgs/empty.pb.h>
#include <ignition/msgs/stringmsg.pb.h>
#include <ignition/plugin/Register.hh>
#include <ignition/transport/Node.hh>
#include <sdf/Element.hh>

namespace pac_gazebo_grasp
{
namespace sim = ignition::gazebo;
namespace components = ignition::gazebo::components;

class ConfirmedGrasp final : public sim::System, public sim::ISystemConfigure,
                            public sim::ISystemPreUpdate, public sim::ISystemPostUpdate
{
 public:
  void Configure(const sim::Entity &entity,
      const std::shared_ptr<const sdf::Element> &sdf,
      sim::EntityComponentManager &ecm, sim::EventManager &) override
  {
    const sim::Model model(entity);
    if (!model.Valid(ecm)) return;
    for (const auto &key : {"parent_link", "child_model", "child_link",
                            "attach_topic", "detach_topic", "output_topic"})
      if (!sdf->HasElement(key)) return;
    parent = model.LinkByName(ecm, sdf->Get<std::string>("parent_link"));
    if (parent == sim::kNullEntity) return;
    childModel = sdf->Get<std::string>("child_model");
    childLink = sdf->Get<std::string>("child_link");
    publisher = node.Advertise<ignition::msgs::StringMsg>(sdf->Get<std::string>("output_topic"));
    const bool attachOk = node.Subscribe(sdf->Get<std::string>("attach_topic"),
        &ConfirmedGrasp::OnAttach, this);
    const bool detachOk = node.Subscribe(sdf->Get<std::string>("detach_topic"),
        &ConfirmedGrasp::OnDetach, this);
    valid = attachOk && detachOk && static_cast<bool>(publisher);
    // No joint and no attach request are created at startup. Stock cartons
    // cannot become an unintended multi-box load on the arm during startup.
  }

  void PreUpdate(const sim::UpdateInfo &info, sim::EntityComponentManager &ecm) override
  {
    if (!valid || info.paused) return;
    if (child == sim::kNullEntity || !ecm.HasEntity(child))
    {
      const auto model = ecm.EntityByComponents(components::Model(), components::Name(childModel));
      if (model == sim::kNullEntity) return;
      child = ecm.EntityByComponents(components::Link(), components::ParentEntity(model),
                                     components::Name(childLink));
      if (child == sim::kNullEntity) return;
    }
    if (joint != sim::kNullEntity && !ecm.HasEntity(joint)) joint = sim::kNullEntity;
    const int command = requested.exchange(0);
    if (command == 1 && joint == sim::kNullEntity)
    {
      // Physics creates a fixed connection at the current relative pose.
      // The executor ensures an air gap before attachment (DART requirement).
      joint = ecm.CreateEntity();
      ecm.CreateComponent(joint, components::DetachableJoint({parent, child, "fixed"}));
      reply = true;
    }
    else if (command == 2 && joint != sim::kNullEntity)
    {
      ecm.RequestRemoveEntity(joint);
      removing = true;
      reply = true;
    }
    else if (command != 0) reply = true;  // idempotent command still gets feedback
  }

  void PostUpdate(const sim::UpdateInfo &info, const sim::EntityComponentManager &ecm) override
  {
    if (!valid || info.paused || child == sim::kNullEntity || !ecm.HasEntity(child)) return;
    const bool attached = joint != sim::kNullEntity && ecm.HasEntity(joint)
                          && ecm.Component<components::DetachableJoint>(joint) != nullptr;
    // RequestRemoveEntity may take another update; do not acknowledge release
    // while the fixed-joint entity is still present.
    if (removing && attached) return;
    if (!attached) removing = false;
    if (reply || attached != lastAttached || info.simTime-lastPublish >= std::chrono::seconds(1))
    {
      ignition::msgs::StringMsg state;
      state.set_data(attached ? "attached" : "detached");
      publisher.Publish(state);
      lastAttached = attached;
      lastPublish = info.simTime;
      reply = false;
    }
  }

 private:
  void OnAttach(const ignition::msgs::Empty &) { requested.store(1); }
  void OnDetach(const ignition::msgs::Empty &) { requested.store(2); }
  ignition::transport::Node node;
  ignition::transport::Node::Publisher publisher;
  sim::Entity parent{sim::kNullEntity}, child{sim::kNullEntity}, joint{sim::kNullEntity};
  std::string childModel, childLink;
  std::atomic<int> requested{0};
  bool valid{false}, removing{false}, reply{true}, lastAttached{false};
  std::chrono::steady_clock::duration lastPublish{};
};
}  // namespace pac_gazebo_grasp

IGNITION_ADD_PLUGIN(pac_gazebo_grasp::ConfirmedGrasp, ignition::gazebo::System,
    pac_gazebo_grasp::ConfirmedGrasp::ISystemConfigure,
    pac_gazebo_grasp::ConfirmedGrasp::ISystemPreUpdate,
    pac_gazebo_grasp::ConfirmedGrasp::ISystemPostUpdate)
IGNITION_ADD_PLUGIN_ALIAS(pac_gazebo_grasp::ConfirmedGrasp, "pac_gazebo_grasp::ConfirmedGrasp")
